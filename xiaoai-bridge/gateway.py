# -*- coding: utf-8 -*-
"""
天声小爱语音网关 v0.2
=================
链路：小爱音箱 -> 小米云 -> xiaogpt -> 本网关(:8008) -> CodeBuddy TUI 服务 -> 回文本 -> 音箱 TTS

v0.2 变化：不再调用 codebuddy CLI 子进程，改为直连运行中的 CodeBuddy TUI
内置 REST API（端口读 ~/.codebuddy/web-ui-port.json）。
前置条件：codebuddy.cmd 窗口保持开启（最小化即可）。
"""

import json
import re
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------- 配置区 ----------------
LISTEN_PORT = 8008              # 本网关监听端口（xiaogpt 的 openai_api_base 指向这里）
API_KEY = "local-only"          # 仅本机使用
PORT_FILE = Path.home() / ".codebuddy" / "web-ui-port.json"

RUN_TIMEOUT = 150               # 任务执行上限（秒）
STREAM_CONNECT_TIMEOUT = 90     # 连接事件流的超时（CodeBuddy 思考期间可能无事件）
MAX_REPLY_CHARS = 200           # 返回给音箱的文本上限（过长小爱 TTS 会截断）

# 语音场景约束：回答会被念出来，必须口语化、短
# 这是兜底人设；正常会优先使用 xiaogpt 传来的 system prompt（即界面里的「助手人设」）
VOICE_SYSTEM_PREFIX = (
    "你是天声小爱（TianshengXiaoai），我的专属AI助手，我通过小爱音箱与你语音对话。"
    "你性格：直接、务实、简洁、带点幽默。"
    "你的回答将被语音播报，控制在80字以内，口语化，不要列表、markdown和链接。"
)

UA_HEADERS = {
    "x-codebuddy-request": "1",
    "Content-Type": "application/json",
    "User-Agent": "tiansheng-xiaoai-gateway/1.0",
}


def get_tui_port() -> int:
    """确定 CodeBuddy 服务端口。

    优先级：tui_port.txt（无窗口守护实例写入）> web-ui-port.json（手动 TUI）> 55002
    """
    local = Path(__file__).parent / "tui_port.txt"
    if local.exists():
        try:
            return int(local.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    try:
        data = json.loads(PORT_FILE.read_text(encoding="utf-8"))
        return int(data.get("port") or data.get("webUiPort") or 55002)
    except Exception:
        return 55002


def _http(method: str, url: str, body: dict | None = None, timeout: int = 15):
    """最小 HTTP 封装，返回 (status, raw_bytes)。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=UA_HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        # 超时/连接中断：返回 0，交由调用方决定是否重试（不要直接抛断流程）
        return 0, str(e).encode("utf-8", "replace")


def ask_workbuddy(question: str, system_prompt: str = "") -> str:
    """向 CodeBuddy TUI 服务提交任务并等待语音化回复。

    system_prompt 来自 xiaogpt 的 system 消息（界面「助手人设」），
    为空时回落到内置 VOICE_SYSTEM_PREFIX。
    """
    port = get_tui_port()
    base = f"http://127.0.0.1:{port}"
    msg_id = "voice-" + uuid.uuid4().hex[:12]
    prefix = (system_prompt or "").strip() or VOICE_SYSTEM_PREFIX

    # 1. 提交任务
    status, raw = _http("POST", f"{base}/api/v1/runs",
                        {"id": msg_id, "type": "user",
                         "text": f"{prefix}\n\n用户的问题：{question}"})
    try:
        run_id = json.loads(raw)["data"]["runId"]
    except Exception:
        snippet = raw.decode("utf-8", "replace")[:120]
        if "connection" in snippet.lower() or status == 0:
            return "助手不在线，请确认电脑上的命令行窗口开着。"
        return f"派活失败：{snippet}"

    # 2. 订阅事件流，等 completed
    deadline = time.time() + RUN_TIMEOUT
    while time.time() < deadline:
        st, raw2 = _http("GET", f"{base}/api/v1/runs/{run_id}/stream",
                         timeout=STREAM_CONNECT_TIMEOUT)
        text = raw2.decode("utf-8", "replace")
        # SSE 事件可能一次连接内推完；也可能是“已完成”404
        m = re.search(r'"status":"completed".*?"markdown":"(.*?)"}', text, re.S)
        if m:
            return _clean(m.group(1))
        if '"status":"failed"' in text or '"status":"error"' in text:
            return "这个任务没跑成，稍后再试一次。"
        # run 已结束但没订上流：读会话历史兜底
        if st == 404 or "Run not found" in text:
            return _fallback_by_history(base, msg_id)
        time.sleep(1)
    return "这个任务比较耗时，我先记下了。稍后你可以在电脑上查看进度。"


def _fallback_by_history(base: str, msg_id: str) -> str:
    """从最近会话历史里找本条消息的回复。"""
    st, raw = _http("GET", f"{base}/api/v1/sessions", timeout=10)
    try:
        sessions = json.loads(raw)["data"]["sessions"]
    except Exception:
        return "任务已完成，但没取到回复，请到电脑上查看。"
    for s in sessions[:3]:
        st2, raw2 = _http("GET", f"{base}/api/v1/sessions/{s['id']}/history", timeout=10)
        try:
            for r in json.loads(raw2)["data"].get("requests", []):
                # history 不带消息 id，按内容启发式兜底：取最近一条
                return _clean(r.get("finalReply", ""))
        except Exception:
            continue
    return "任务已完成，但没取到回复，请到电脑上查看。"


def _clean(text: str) -> str:
    """清理 markdown 符号并截断，适配语音播报。"""
    text = text.replace("\\n", " ").replace("\\\"", '"')
    text = re.sub(r"[*#`_\[\]()>-]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_REPLY_CHARS:
        text = text[: MAX_REPLY_CHARS - 6] + "……细节已生成文件，电脑上查看。"
    return text


def extract_user_question(payload: dict) -> str:
    """从 OpenAI chat/completions 请求里取最后一条 user 消息。"""
    for msg in reversed(payload.get("messages", [])):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") for p in content if isinstance(p, dict)
                )
            return content.strip()
    return ""


def extract_system_prompt(payload: dict) -> str:
    """取 system 消息（xiaogpt 会把界面里的「助手人设」作为 system 发出）。

    取到就用人设，取不到则回落到内置的 VOICE_SYSTEM_PREFIX。
    """
    for msg in payload.get("messages", []):
        if msg.get("role") == "system":
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") for p in content if isinstance(p, dict)
                )
            text = (content or "").strip()
            if text:
                return text
    return ""


def _chat_reply(text: str) -> dict:
    now = int(time.time())
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": now,
        "model": "workbuddy",
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": text},
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[网关] {fmt % args}")

    def _send_json(self, code: int, body: dict):
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path in ("/v1/models", "/models"):
            self._send_json(200, {
                "object": "list",
                "data": [{"id": "workbuddy", "object": "model", "owned_by": "local"}],
            })
        elif self.path == "/health":
            port = get_tui_port()
            try:
                st, raw = _http("GET", f"http://127.0.0.1:{port}/api/v1/health", timeout=5)
                tui = json.loads(raw).get("data", {}).get("status") == "ok"
            except Exception:
                tui = False
            self._send_json(200, {
                "gateway": "ok",
                "codebuddy_tui": tui,
                "tui_port": port,
                "hint": None if tui else "请打开 codebuddy.cmd 窗口并保持开启",
            })
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send_json(404, {"error": "not found"})
            return

        auth = self.headers.get("Authorization", "")
        if API_KEY and API_KEY not in auth:
            self._send_json(401, {"error": {"message": "bad api key"}})
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(400, {"error": {"message": "bad json"}})
            return

        question = extract_user_question(payload)
        if not question:
            self._send_json(200, _chat_reply("没听清你说什么，再说一遍？"))
            return

        system_prompt = extract_system_prompt(payload)
        source = (self.headers.get("X-Source") or "speaker").strip()
        print(f"\n[任务] 收到语音指令（{source}）: {question}")
        answer = ask_workbuddy(question, system_prompt)
        print(f"[任务] 回答: {answer}")
        log_dialogue(question, answer, source)

        if payload.get("stream"):
            self._send_stream(answer)
        else:
            self._send_json(200, _chat_reply(answer))

    def _send_stream(self, text: str):
        """OpenAI 兼容的 SSE 流式响应（mi-gpt 等客户端需要）。"""
        cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        now = int(time.time())

        def chunk(delta: dict, finish=None) -> str:
            body = {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": now,
                "model": "workbuddy",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"

        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True  # HTTP/1.0：靠关闭连接标示响应结束
            self.wfile.write(chunk({"role": "assistant", "content": ""}).encode("utf-8"))
            # 分段推送，让客户端有流式体验
            step = 24
            for i in range(0, len(text), step):
                self.wfile.write(chunk({"content": text[i:i + step]}).encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(chunk({}, "stop").encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except Exception as e:
            print(f"[流式] 推送失败: {e}")


DIALOGUE_LOG = Path(__file__).parent / "dialogue_log.jsonl"


def log_dialogue(question: str, answer: str, source: str = "speaker"):
    """问答落盘，供 history.py / 天声小爱展示。"""
    try:
        entry = {
            "time": int(time.time() * 1000),
            "query": question,
            "answer": answer,
            "source": source,
        }
        with open(DIALOGUE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[日志] 写入失败: {e}")


def main():
    from paths import watch_parent

    watch_parent()
    server = ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), Handler)
    print(f"天声小爱语音网关 v0.2 已启动: http://127.0.0.1:{LISTEN_PORT}")
    print(f"CodeBuddy TUI 服务端口: {get_tui_port()}")
    print("前置条件: codebuddy.cmd 窗口保持开启（最小化即可）")
    print("等待语音指令... Ctrl+C 退出")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n网关已停止")


if __name__ == "__main__":
    main()

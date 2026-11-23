"""P9a: the shared LLM lane (one Ollama request at a time) and the JSON-constrained completion.
No Ollama needed: requests go to httpx.MockTransport."""
import asyncio
import json
from contextlib import aclosing

import httpx

from backend import narrate


def _ndjson(*tokens: str, done: bool = True) -> bytes:
    lines = [json.dumps({"message": {"content": t}, "done": False}) for t in tokens]
    if done:
        lines.append(json.dumps({"done": True, "prompt_eval_count": 50, "prompt_eval_duration": 1e8, "eval_count": 2}))
    return ("\n".join(lines) + "\n").encode()


class Slow(httpx.AsyncByteStream):
    def __init__(self, label: str, delay: float = 0.2):
        self.label, self.delay = label, delay

    async def __aiter__(self):
        await asyncio.sleep(self.delay)
        yield _ndjson(self.label)


def _narrator(handler) -> narrate.Narrator:
    n = narrate.Narrator.from_config()
    n.client = httpx.AsyncClient(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    return n


def test_two_streams_run_one_after_the_other():
    order: list[str] = []

    def handler(req):
        return httpx.Response(200, stream=Slow(json.loads(req.content)["messages"][1]["content"]))

    async def consume(n, prompt):
        async with aclosing(n.stream(prompt, n.model)) as evs:
            async for ev, data in evs:
                order.append(f"{prompt}:{ev}")
        return data

    async def main():
        n = _narrator(handler)
        a, b = await asyncio.gather(consume(n, "A"), consume(n, "B"))
        await n.aclose()
        return a, b

    a, b = asyncio.run(main())
    assert order == ["A:token", "A:done", "B:token", "B:done"]
    assert a["wait_ms"] < 100 and b["wait_ms"] >= 150  # B waited for A's ~200 ms request


def test_busy_lane_times_out_as_event():
    async def main():
        n = _narrator(lambda req: httpx.Response(200, content=_ndjson("x")))
        await n._lane().acquire()  # someone else holds the lane
        evs = [ev async for ev in n.stream("p", n.model, limits=(0.1, 0.2))]
        n._lane().release()
        await n.aclose()
        return evs

    assert asyncio.run(main()) == [("error", {"message": "AI note unavailable (busy)"})]


def test_abandoned_stream_releases_lane():
    async def main():
        n = _narrator(lambda req: httpx.Response(200, content=_ndjson("a", "b", "c")))
        async with aclosing(n.stream("p", n.model)) as evs:
            async for _ in evs:
                break  # the client went away after one token
        free = not n._lane().locked()
        await n.aclose()
        return free

    assert asyncio.run(main()) is True


def test_stream_num_predict_override():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=_ndjson("x"))

    async def main():
        n = _narrator(handler)
        _ = [ev async for ev in n.stream("p", n.model, num_predict=140)]
        await n.aclose()

    asyncio.run(main())
    assert seen["body"]["options"]["num_predict"] == 140


SCHEMA = {"type": "object", "properties": {"industry": {"type": "string"}}, "required": ["industry"]}


def _json_call(handler, timeout: float = 2.0):
    async def main():
        n = _narrator(handler)
        out = await n.complete_json("sys", "user", SCHEMA, n.model, 40, timeout)
        free = not n._lane().locked()
        await n.aclose()
        return out, free

    return asyncio.run(main())


def test_complete_json_parses_and_sends_schema():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"message": {"content": '{"industry": "dairy"}'}, "done": True,
                                         "prompt_eval_count": 200, "eval_count": 9})

    (parsed, info), free = _json_call(handler)
    assert parsed == {"industry": "dairy"} and info["gen_tokens"] == 9 and free
    b = seen["body"]
    assert b["stream"] is False and b["format"] == SCHEMA and b["options"]["num_predict"] == 40
    assert b["options"]["temperature"] == 0 and b["messages"][0] == {"role": "system", "content": "sys"}


def test_complete_json_failures_are_none_and_release_lane():
    bad = httpx.Response(200, json={"message": {"content": "not json"}})
    (parsed, info), free = _json_call(lambda req: bad)
    assert parsed is None and info["error"] == "offline" and free
    (parsed, info), free = _json_call(lambda req: httpx.Response(500, json={}))
    assert parsed is None and info["error"] == "http 500" and free

    def refused(req):
        raise httpx.ConnectError("refused")

    (parsed, info), free = _json_call(refused)
    assert parsed is None and info["error"] == "offline" and free
    (parsed, info), free = _json_call(lambda req: httpx.Response(200, json={"message": {"content": "[1, 2]"}}))
    assert parsed is None and free


def test_complete_json_timeout():
    async def slow_handler(req):
        await asyncio.sleep(3)  # MockTransport ignores httpx timeouts: the wait_for backstop (timeout + 1 s) ends it
        return httpx.Response(200, json={"message": {"content": "{}"}})

    (parsed, info), free = _json_call(slow_handler, timeout=0.2)
    assert parsed is None and info["error"] == "timeout" and free

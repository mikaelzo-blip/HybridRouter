# Stream Disconnect Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `httpx.StreamClosed` crash yang menyebabkan HybridRouter sering disconnect saat development — stream upstream putus di tengah jalan tanpa recovery yang benar.

**Architecture:** Dua-lapis: (1) `chunk_generator` di `upstream.py` harus catch semua httpx stream errors dan wrap menjadi SSE error event sebelum generator ditutup, supaya client tahu koneksi mati bukan hanya timeout. (2) Tambah reconnect/fallback logic saat stream putus mid-flight dengan mencoba alias fallback berikutnya.

**Tech Stack:** Python asyncio, httpx streaming, FastAPI `StreamingResponse`, pytest-asyncio.

**Spec:** N/A (bug fix dari live traceback `httpx.StreamClosed` di `upstream.py:127`)

## Global Constraints

- Python ≥ 3.11
- Tidak boleh ubah signature publik `forward_stream()` — return type tetap `tuple[AsyncGenerator, str, dict]`
- Tidak boleh ubah `app.py` kecuali yang eksplisit disebutkan
- Test command: `uv run pytest tests/test_streaming.py -v`

## Review Focus

1. Stream putus setelah chunk pertama sudah dikirim — client harus terima SSE error event, bukan silent truncation
2. Fallback alias tersedia tapi stream sudah half-open — harus close dulu sebelum retry
3. `stream_cm.__aexit__` dipanggil dua kali (oleh finally dan oleh error path) — harus idempotent
4. `httpx.ReadTimeout` (bukan hanya `StreamClosed`) — harus ikut dicatch
5. Generator di-GC sebelum exhausted (client disconnect duluan) — `finally` harus tetap jalan

---

## Task 1: Catch stream errors di `chunk_generator` dan emit SSE error event

**Files:**
- Modify: `src/server/upstream.py:125-130`
- Test: `tests/test_streaming.py` (tambah test baru di bawah)

**Interfaces:**
- Consumes: `resp.aiter_bytes()` — async generator bytes dari httpx stream
- Produces: `chunk_generator()` tetap async generator bytes, tapi jika error terjadi mid-stream ia yield satu SSE error event lalu stop

- [x] **Step 1: Tulis failing test — stream putus di tengah**

```python
@pytest.mark.asyncio
async def test_forward_stream_mid_stream_disconnect(mock_config):
    """Generator harus yield SSE error event jika stream putus mid-flight."""
    upstream = UpstreamClient(mock_config)

    async def mock_aiter_bytes_broken():
        yield b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n'
        raise httpx.StreamClosed()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/event-stream"}
    mock_resp.aiter_bytes = mock_aiter_bytes_broken

    class MockStreamContext:
        async def __aenter__(self): return mock_resp
        async def __aexit__(self, *a): pass

    with patch.object(upstream.client, "stream", return_value=MockStreamContext()):
        gen, _, _ = await upstream.forward_stream(
            "gemini_executor",
            {"model": "auto", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        )
        collected = []
        async for chunk in gen:
            collected.append(chunk)

    body = b"".join(collected)
    assert b"Hello" in body                         # chunk pertama sampai
    assert b"upstream_stream_error" in body         # SSE error event dikirim
    await upstream.close()
```

- [x] **Step 2: Jalankan test — pastikan FAIL**

```bash
uv run pytest tests/test_streaming.py::test_forward_stream_mid_stream_disconnect -v
```
Expected: FAIL — test baru, belum ada di file; kalau sudah ada pastikan assert `upstream_stream_error` gagal.

- [x] **Step 3: Implementasi fix di `upstream.py`**

Ganti `chunk_generator` (lines 125–130) menjadi:

```python
async def chunk_generator():
    try:
        async for chunk in resp.aiter_bytes():
            yield chunk
    except (httpx.StreamClosed, httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ReadError) as exc:
        logger.warning("Upstream stream disconnected mid-flight (alias=%s): %s", current_alias, exc)
        # Emit SSE error event agar client tidak silent-truncate
        error_payload = json.dumps({"error": {"type": "upstream_stream_error", "message": str(exc)}})
        yield f"data: {error_payload}\n\n".encode()
    finally:
        await stream_cm.__aexit__(None, None, None)
```

Tambah `import json` di atas file jika belum ada.

- [x] **Step 4: Jalankan test — pastikan PASS**

```bash
uv run pytest tests/test_streaming.py -v
```
Expected: semua PASS termasuk test baru.

- [x] **Step 5: Commit**

```bash
git add src/server/upstream.py tests/test_streaming.py
git commit -m "fix(upstream): catch httpx stream errors mid-flight and emit SSE error event"
```

---

## Task 2: Guard `stream_cm.__aexit__` agar idempotent (double-close safe)

**Files:**
- Modify: `src/server/upstream.py` — wrap `__aexit__` call dalam try/except

**Interfaces:**
- Consumes: `stream_cm.__aexit__` — bisa dipanggil lebih dari sekali jika error path dan finally overlap
- Produces: tidak ada exception baru dari double-close

- [ ] **Step 1: Tulis failing test — double aexit tidak crash**

```python
@pytest.mark.asyncio
async def test_forward_stream_double_close_safe(mock_config):
    """__aexit__ dipanggil dua kali tidak boleh raise."""
    upstream = UpstreamClient(mock_config)
    aexit_count = []

    async def mock_aiter_bytes_broken():
        raise httpx.StreamClosed()
        yield  # make it a generator

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.aiter_bytes = mock_aiter_bytes_broken

    class MockStreamContextDoubleClose:
        async def __aenter__(self): return mock_resp
        async def __aexit__(self, *a):
            aexit_count.append(1)
            if len(aexit_count) > 1:
                raise RuntimeError("double close!")

    with patch.object(upstream.client, "stream", return_value=MockStreamContextDoubleClose()):
        gen, _, _ = await upstream.forward_stream(
            "gemini_executor",
            {"model": "auto", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        )
        collected = []
        async for chunk in gen:
            collected.append(chunk)
    # Tidak boleh raise RuntimeError
    await upstream.close()
```

- [ ] **Step 2: Jalankan test — pastikan FAIL**

```bash
uv run pytest tests/test_streaming.py::test_forward_stream_double_close_safe -v
```

- [ ] **Step 3: Implementasi — wrap aexit di finally**

Ganti `finally` block di `chunk_generator`:

```python
finally:
    try:
        await stream_cm.__aexit__(None, None, None)
    except Exception:
        pass
```

- [ ] **Step 4: Jalankan semua test**

```bash
uv run pytest tests/test_streaming.py -v
```
Expected: semua PASS.

- [ ] **Step 5: Commit**

```bash
git add src/server/upstream.py tests/test_streaming.py
git commit -m "fix(upstream): guard stream_cm.__aexit__ against double-close"
```

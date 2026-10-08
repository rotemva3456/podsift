# Portable podcast engine

The Python package at the project root distributes the canonical
`companion.engine` implementation. It does not copy or generate another engine;
the companion service keeps importing the same modules.

Offline transcript parsing, selection, media validation, and rendering use only the
Python standard library. Rendering and media probing require `ffmpeg` and `ffprobe` on
`PATH`; they are executable prerequisites rather than Python dependencies. HTTP feed,
transcript, and enclosure fetching is isolated in `companion.engine.net` and requires
the optional `network` extra, which installs `httpx`.

Build a wheel with:

```bash
python3 -m pip wheel --no-deps --wheel-dir dist .
```

After installation, import it with:

```python
from companion import engine
```

Install the locally built wheel with HTTP fetching enabled using its file path; this
does not assume that `podsift-engine` has been published to PyPI:

```bash
python3 -m pip install './dist/podsift_engine-0.1.0-py3-none-any.whl[network]'
```

Run the isolated acceptance check from the exported application root:

```bash
python3 -m pip install 'setuptools>=77'
python3 scripts/verify-engine-wheel.py
```

The check builds the current source, installs its wheel into a fresh virtual
environment, clears `PYTHONPATH`, runs from `/`, and verifies that the imported module
comes from that environment. It creates its own synthetic tone, plans around an
excluded interval, clamps a deliberately overlong transcript segment, rejects changed
media, and renders the bounded cut without network access. It then verifies the
`network` extra metadata and performs a `safe_read` through `httpx.MockTransport`, with
real socket access blocked. Its printed proof directory contains the wheel, rendered
MP3, hashes, `result.json`, and `network-result.json`.

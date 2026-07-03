# UnitTest_gen codebase chatbot (standalone)

Local RAG chatbot over documentation (`.md`, `.puml`) and optional Python source. Uses **two** llama.cpp servers:

| Role | Default model | Port |
|------|----------------|------|
| Chat | `llama-3.2-3b-instruct-q4_k_m.gguf` | 8082 |
| Embeddings | `nomic-embed-text-v1.5.Q6_K.gguf` | 8081 |

Copy this folder as a bundle with `doc/` beside `chatbot/`:

```text
bot/
  chatbot/          ← this directory (scripts + requirements.txt)
  doc/              ← markdown / PlantUML to index
  log/              ← server logs (created automatically)
```

---

## Tools in this folder

| File | Purpose |
|------|---------|
| `codebase_chatbot.py` | Terminal chat (REPL) |
| `codebase_chatbot_gui.py` | Tkinter GUI |
| `codebase_chatbot_lib.py` | Runtime, indexing, retrieval |
| `requirements.txt` | Pip dependencies only |

The chatbot can **auto-start** both llama servers when you run it. Use `--no-manage-server` if you start servers yourself.

---

## Commands (all platforms)

Run from the `chatbot/` directory (or pass full paths).

```bash
# Help
python codebase_chatbot.py -h
python codebase_chatbot_gui.py -h

# First run or after doc changes — rebuild index cache
python codebase_chatbot.py --reindex

# Index only doc/ next to bot/ (default for standalone copy)
python codebase_chatbot.py --reindex

# Index full UnitTest_gen tree (docs + Python)
python codebase_chatbot.py --codebase /path/to/UnitTest_gen --reindex

# GUI
python codebase_chatbot_gui.py --reindex

# Do not start/stop llama-server from Python
python codebase_chatbot.py --no-manage-server --reindex

# Background servers (no extra terminal windows)
python codebase_chatbot.py -nst --reindex

# --- Share knowledge without source code (see section below) ---
# Build bundle on YOUR machine (needs full codebase + embedding server):
python codebase_chatbot.py --codebase /path/to/UnitTest_gen --reindex \
  --export-knowledge-bundle knowledge.bundle.json

# Recipient runs chatbot with bundle only (no doc/, no .py source):
python codebase_chatbot.py --knowledge-bundle knowledge.bundle.json
python codebase_chatbot_gui.py --knowledge-bundle knowledge.bundle.json
```

**Useful environment variables**

| Variable | Default | Meaning |
|----------|---------|---------|
| `CHATBOT_MODEL_PATH` | `~/models/llama-3.2-3b-instruct-q4_k_m.gguf` | Chat GGUF |
| `CHATBOT_EMBEDDING_MODEL_PATH` | `~/models/nomic-embed-text-v1.5.Q6_K.gguf` | Embedding GGUF |
| `CHATBOT_BASE_URL` | `http://127.0.0.1:8082/v1` | Chat API |
| `CHATBOT_EMBEDDING_BASE_URL` | `http://127.0.0.1:8081/v1` | Embedding API |
| `CHATBOT_EMBEDDING_MODEL` | `nomic-embed-text` | Model name for `/v1/embeddings` |
| `CHATBOT_SERVER_CWD` | `~/llama.cpp` | Directory containing `build/bin/llama-server` |
| `CHATBOT_ENABLE_EMBEDDINGS` | `1` | Set `0` for keyword-only retrieval |

---

## Example chat session

```text
$ cd bot/chatbot
$ python codebase_chatbot.py --reindex

🚀 Started coding server ...
🚀 Started embedding server ...
✅ coding server is reachable.
✅ embedding server is reachable.
Chat model: llama-3.2-3b-instruct-q4_k_m.gguf @ http://127.0.0.1:8082/v1
Embeddings: nomic-embed-text @ http://127.0.0.1:8081/v1
Indexing /home/you/bot/doc ...
Ready: 3 files indexed. Cache: .../chatbot/.cache/codebase_chat_index_....json
Retrieval: embedding vectors + keyword overlap.
Ask questions about UnitTest_gen (exit/quit to leave).

you> What does MODEL_STUCK_DETECTOR_ENABLED control?

assistant> It is the global switch for stream repetition / stuck detection in
core/model_runtime.py. CLI: --enable-stuck-detector. Env: TESTGEN_ENABLE_STUCK_DETECTOR.

you> quit
```

Ask with **exact names** from the docs (`MODEL_STUCK_DETECTOR_ENABLED`, file paths) for best results when embeddings are off.

---

## Sharing with others (no source code)

You can ship **answers without** sharing `UnitTest_gen` Python/Kotlin source or raw `doc/` files.

### How it works

1. **On your machine** — index the full tree with embeddings, then export a **knowledge bundle** (JSON database of text snippets + vectors).
2. **Give recipients** — `chatbot/` scripts, `requirements.txt`, `README.md`, and `knowledge.bundle.json` only.
3. **They run** — `--knowledge-bundle knowledge.bundle.json` (no `--codebase`, no `doc/` folder).

The bundle is a portable “DB” file: relative paths, truncated text chunks, and precomputed embedding vectors. Recipients still need their own **llama chat + nomic embed** servers (or you document manual server setup); they do **not** need your proprietary source.

### Build the bundle (maintainer)

```bash
cd bot/chatbot
source .venv/bin/activate   # Ubuntu; see Windows section for .venv\Scripts\activate

python codebase_chatbot.py \
  --codebase /path/to/UnitTest_gen \
  --reindex \
  --export-knowledge-bundle knowledge.bundle.json
```

Use `--no-manage-server` if servers are already running. Re-export when docs or code change.

### Package for recipients

```text
bot-for-others/
  chatbot/
    codebase_chatbot.py
    codebase_chatbot_gui.py
    codebase_chatbot_lib.py
    requirements.txt
    README.md
    knowledge.bundle.json    ← only this holds your indexed knowledge
```

Optional: hide `.py` with **PyInstaller** (`pyinstaller --onefile codebase_chatbot.py`) — bundle file stays external.

### Recipient usage

```bash
pip install -r requirements.txt
python codebase_chatbot.py --knowledge-bundle knowledge.bundle.json
```

### Limits

| Topic | Note |
|-------|------|
| Security | Bundle contains **text excerpts** from your indexed files — not full source, but still sensitive. |
| Updates | Re-export bundle when knowledge changes; recipients replace one file. |
| Vectors | Built with your embedding model; recipients should use the **same** nomic model for best retrieval. |
| Chatbot code | Recipients still run Python chatbot scripts unless you ship an executable. |

---

# Ubuntu / Debian

## 1. Python and pip

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv
```

## 2. Tkinter (GUI only)

`tkinter` is **not** installed via pip.

```bash
sudo apt install -y python3-tk
python3 -c "import tkinter; print('tkinter ok')"
```

## 3. Python packages

```bash
cd bot/chatbot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 4. llama.cpp

Build [llama.cpp](https://github.com/ggml-org/llama.cpp) and note the path to `build/bin/llama-server`:

```bash
git clone https://github.com/ggml-org/llama.cpp.git ~/llama.cpp
cd ~/llama.cpp
cmake -B build
cmake --build build -j --config Release
export CHATBOT_SERVER_CWD=~/llama.cpp
```

## 5. Download models (Hugging Face)

Install the CLI (included in `requirements.txt`):

```bash
pip install huggingface_hub
mkdir -p ~/models
```

**Chat model** (Llama 3.2 3B Instruct, Q4_K_M ~2 GB):

```bash
huggingface-cli download bartowski/Llama-3.2-3B-Instruct-GGUF \
  Llama-3.2-3B-Instruct-Q4_K_M.gguf \
  --local-dir ~/models \
  --local-dir-use-symlinks False
```

Rename or point env if the filename differs:

```bash
export CHATBOT_MODEL_PATH=~/models/Llama-3.2-3B-Instruct-Q4_K_M.gguf
```

**Embedding model** (nomic-embed-text v1.5, Q6_K ~110 MB):

```bash
huggingface-cli download nomic-ai/nomic-embed-text-v1.5-GGUF \
  nomic-embed-text-v1.5-Q6_K.gguf \
  --local-dir ~/models \
  --local-dir-use-symlinks False
```

```bash
export CHATBOT_EMBEDDING_MODEL_PATH=~/models/nomic-embed-text-v1.5-Q6_K.gguf
```

Alternative (newer CLI):

```bash
hf download bartowski/Llama-3.2-3B-Instruct-GGUF Llama-3.2-3B-Instruct-Q4_K_M.gguf --local-dir ~/models
hf download nomic-ai/nomic-embed-text-v1.5-GGUF nomic-embed-text-v1.5-Q6_K.gguf --local-dir ~/models
```

## 6. Run

```bash
cd bot/chatbot
source .venv/bin/activate
python codebase_chatbot.py --reindex
```

---

# Windows

## 1. Python

1. Install **Python 3.10+** from [python.org](https://www.python.org/downloads/).
2. Check **“Add python.exe to PATH”** during setup.
3. Verify:

```powershell
python --version
pip --version
```

## 2. Tkinter (GUI only)

On the Python installer, enable **“tcl/tk and IDLE”** (included by default in full installs).

```powershell
python -c "import tkinter; print('tkinter ok')"
```

If that fails, run the installer again → **Modify** → ensure **tcl/tk** is selected.

## 3. Python packages

```powershell
cd bot\chatbot
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## 4. llama.cpp

1. Clone and build [llama.cpp](https://github.com/ggml-org/llama.cpp) with Visual Studio / CMake, or use a prebuilt `llama-server.exe`.
2. Set the folder that contains `llama-server.exe`:

```powershell
$env:CHATBOT_SERVER_CWD = "$env:USERPROFILE\llama.cpp"
```

On Windows the managed command uses `build\bin\llama-server` relative to that folder (adjust `CHATBOT_SERVER_COMMAND` if your layout differs).

## 5. Download models (Hugging Face)

```powershell
pip install huggingface_hub
mkdir $env:USERPROFILE\models
```

**Chat model:**

```powershell
huggingface-cli download bartowski/Llama-3.2-3B-Instruct-GGUF Llama-3.2-3B-Instruct-Q4_K_M.gguf --local-dir $env:USERPROFILE\models --local-dir-use-symlinks False
$env:CHATBOT_MODEL_PATH = "$env:USERPROFILE\models\Llama-3.2-3B-Instruct-Q4_K_M.gguf"
```

**Embedding model:**

```powershell
huggingface-cli download nomic-ai/nomic-embed-text-v1.5-GGUF nomic-embed-text-v1.5-Q6_K.gguf --local-dir $env:USERPROFILE\models --local-dir-use-symlinks False
$env:CHATBOT_EMBEDDING_MODEL_PATH = "$env:USERPROFILE\models\nomic-embed-text-v1.5-Q6_K.gguf"
```

## 6. Run

```powershell
cd bot\chatbot
.\.venv\Scripts\activate
python codebase_chatbot.py --reindex
```

GUI:

```powershell
python codebase_chatbot_gui.py --reindex
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `No module named 'tkinter'` | Install OS tk package (Ubuntu: `python3-tk`; Windows: enable tcl/tk in Python installer). |
| `Pooling type 'none' is not OAI compatible` | Chat model used for embeddings — use nomic on 8081 (current code does this automatically). |
| `0 files indexed` | Ensure `doc/` is next to `chatbot/` or pass `--codebase /path/to/docs`. |
| Wrong answers | Run `--reindex` with `--codebase` pointing at full `UnitTest_gen`; confirm embedding server is up. |
| Port already in use | Stop old `llama-server` or change `CHATBOT_BASE_URL` / `CHATBOT_EMBEDDING_BASE_URL`. |

## Manual servers (optional)

If you prefer to start servers yourself (`--no-manage-server`):

**Ubuntu — chat (8082):**

```bash
cd ~/llama.cpp
./build/bin/llama-server -m ~/models/Llama-3.2-3B-Instruct-Q4_K_M.gguf \
  --port 8082 --host 127.0.0.1 --ctx-size 8192 --batch-size 512
```

**Ubuntu — embeddings (8081):**

```bash
./build/bin/llama-server -m ~/models/nomic-embed-text-v1.5-Q6_K.gguf \
  --embedding --port 8081 --host 127.0.0.1 --ctx-size 8192 \
  --batch-size 2048 --ubatch-size 2048 --rope-scaling yarn --rope-freq-scale 0.75
```

**Windows:** same flags; use `build\bin\llama-server.exe` and Windows paths.

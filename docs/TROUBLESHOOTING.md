# Troubleshooting

## Ollama service not running

**Symptom:** `ConnectionRefusedError` or `requests.exceptions.ConnectionError` on startup.

```
✗ Ollama not reachable at http://127.0.0.1:11434
```

**Fix:**
```bash
sudo systemctl start ollama
sudo systemctl status ollama   # verify it's active
```

If Ollama keeps restarting, check logs:
```bash
journalctl -u ollama -n 50
```

---

## Transient ConnectionError mid-run (Ollama restart)

**Symptom:** A single section fails with `ConnectionError` then recovers on its own in the next paper.

**Cause:** Ollama was restarted while a request was in-flight (e.g. by `systemctl restart ollama` or a watchdog). The pipeline retries automatically; no action needed unless failures persist.

**Reference:** Fixed in commit `6927bc9`.

---

## Model not found / auto-download cascade

**Symptom:** First request for a model hangs for minutes, then times out. Subsequent papers fail similarly.

**Cause:** Ollama silently downloads the model on first use, blocking the request. The pipeline's timeout fires before the download completes.

**Fix:** Pull models explicitly before running:
```bash
ollama pull deepseek-r1:8b
ollama pull deepseek-r1:14b
ollama pull gemma4:31b-it-q4_K_M
ollama pull qwen3-coder:30b
```

Verify all models are available:
```bash
ollama list
```

---

## Invalid API options (flash_attention / kv_cache_type)

**Symptom:** Ollama returns a 400 error mentioning `flash_attention` or `kv_cache_type`.

**Cause:** These are model-load-time options, not per-request options. Passing them in the request body is rejected by newer Ollama versions.

**Fix:** Set them in the Ollama systemd override instead:
```ini
# /etc/systemd/system/ollama.service.d/override.conf
[Service]
Environment="OLLAMA_FLASH_ATTENTION=1"
```

Then reload:
```bash
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

**Reference:** Fixed in commit `49b1c20`.

---

## GPU out of memory (OOM)

**Symptom:** Ollama runner crashes or returns errors for large models. `nvidia-smi` shows VRAM fully consumed.

**Guidelines by hardware:**

| Model | Min VRAM | Notes |
|-------|----------|-------|
| `deepseek-r1:8b` | ~6 GB | Fits on single RTX 3080 |
| `deepseek-r1:14b` | ~10 GB | Fits on RTX 3080 10 GB with headroom |
| `gemma4:31b-it-q4_K_M` | ~20 GB | Needs RTX 5080 or dual-GPU span |
| `qwen3-coder:30b` | ~20 GB | Same as above |

**Fix:** Use `--model deepseek-r1:14b` on single-GPU setups to stay within VRAM limits, or enable dual-GPU with `CUDA_VISIBLE_DEVICES=0,1` in the Ollama override.

---

## Graphviz `dot` not installed

**Symptom:** Diagram stage fails with `FileNotFoundError: [Errno 2] No such file or directory: 'dot'`.

**Fix:**
```bash
sudo apt install graphviz
```

Verify:
```bash
dot -V
```

---

## PDF extraction failures

**Symptom:** A paper produces an empty or very short summary. `metadata.json` shows `pages: 0`.

**Causes:**
- Scanned PDF (image-only, no embedded text) — pymupdf cannot extract text from raster pages.
- Corrupted PDF — file is truncated or has invalid structure.
- Password-protected PDF — pymupdf cannot open encrypted files without the password.

**Fix:** Check the PDF manually. Scanned papers require OCR pre-processing (e.g. `ocrmypdf`) before the pipeline can handle them. Corrupted files should be re-downloaded from the source.

---

## Stale diagram slugs on reprocess

**Symptom:** Old diagram filenames persist in `docs/diagrams/` after reprocessing a paper with a different model that generates different diagram titles.

**Fix:** Delete the paper's diagram directory and reprocess:
```bash
rm -rf _processed/<paper-slug>/diagrams/
```

The pipeline will regenerate all diagrams from scratch. **Reference:** Fixed in commit `b3687cf`.

---

## Resuming an interrupted run

The pipeline writes stage completion markers to `metadata.json` per paper. On the next run it skips any stage already marked complete.

To **force a full reprocess** of a specific paper, delete its metadata:
```bash
rm _processed/<paper-slug>/metadata.json
```

To reprocess only one stage, edit `metadata.json` and remove that stage's key, then re-run.

---

## Neo4j container won't start after crash or reboot

**Symptom:** Neo4j container fails to start. `docker compose logs` shows:
```
Error response from daemon: container is marked for removal and cannot be started
```
Or systemd service `paper-processor-neo4j` repeatedly exits with status=1/FAILURE.

**Cause:** A prior run of the container or compose stack left a container in a "marked for removal" state, blocking new attempts to recreate it.

**Fix:** Clean up stale containers and restart:
```bash
docker container prune -f
docker system prune -f
sudo systemctl restart paper-processor-neo4j.service
```

Verify the service is running:
```bash
sudo systemctl status paper-processor-neo4j.service
curl -s http://localhost:7474 | head -3  # HTTP console should respond
nc -zv localhost 7687                     # Bolt protocol should accept connections
```

---

## Permission denied removing Neo4j data directories

**Symptom:** When manually cleaning `neo4j_viz/data` or `neo4j_viz/logs` directories, `rm -rf` fails with many `Permission denied` errors:
```
rm: cannot remove 'data/databases/neo4j/neostore': Permission denied
```

**Cause:** Neo4j container runs with a non-jeb uid (7474), and Docker mounts volumes with that ownership. The local user cannot delete them.

**Fix:** Use sudo to remove the directories:
```bash
cd neo4j_viz/
sudo rm -rf data logs import plugins
```

Or let Docker handle cleanup via compose:
```bash
docker compose down -v  # -v also removes named volumes
```

**Prevention:** Always use `docker compose down -v` when stopping the stack intentionally, rather than manual `rm`.

---

## Checking GPU utilization

```bash
nvtop          # interactive GPU monitor
nvidia-smi     # one-shot snapshot
ollama ps      # which models are loaded and into which GPU
```

#!/bin/bash
# paperproc_howto_wall.sh
# Periodically wall-broadcasts a condensed paper_proc_smrtevict.py operator
# quick-start to every logged-in terminal, so it's visible to someone already
# logged in (not just at next login, which is what the motd entry covers).
# Full version: /etc/update-motd.d/94-paperproc-howto
# Cron'd — see crontab -l (ricky).

MSG="paper_proc quick-start — start backend: ~/programs/paper_proc/docs/sessions/1787167674_pin-model-gtx1060/start_gpu1_backend.sh (safe, 48 tok/s) | launch: cd ~/programs/paper_proc && nohup env OLLAMA_URL=http://127.0.0.1:11435 .venv/bin/python paper_proc_smrtevict.py --model gemma4:e2b-it-qat /home/ricky/Documents/AI-ML_Papers/aug_8_2026 > logs/smrtevict_\$(date +%s).log 2>&1 & disown | monitor: tail -f logs/smrtevict_*.log or tail -f /tmp/ollama_gpu1.log (NOT journalctl -u ollama -f, that's the wrong/shared :11434 daemon) | stop (ONE signal only): pkill -TERM -f paper_proc_smrtevict.py | full ref: ~/Documents/claude_creations/session_1787507023_paper-proc-investigation/ascii_tree.md"

echo "$MSG" | wall

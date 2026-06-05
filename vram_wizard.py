#!/usr/bin/env python3
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# vram_wizard.py
# Interactive CLI wizard and control center to manage the pipeline and viz.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import os
import sys
import subprocess
import time

# ANSI colors for TTY styling
NEON_GREEN = "\033[38;2;0;255;65m"
NEON_MAGENTA = "\033[38;2;255;0;255m"
NEON_CYAN = "\033[38;2;0;255;255m"
NEON_YELLOW = "\033[38;2;255;255;0m"
NEON_ORANGE = "\033[38;2;255;102;0m"
RESET = "\033[0m"
BOLD = "\033[1m"
UNDERLINE = "\033[4m"

DEFAULT_PAPERS_DIR = "/mnt/raid0/monolithic_pdf_folderv3/illoinois_edu"
BACKEND_SCRIPT_PATH = "./docs/sessions/2026-06-04T14-03-40_gpu_telemetry/start_isolated_backends.sh"
FORK_SCRIPT_PATH = "vram_resident_processor.py"

def clear_screen():
    print("\033[H\033[J", end="")

def prompt_choice(prompt_text, options, default_val=1):
    """Prompts the operator to select a choice from a list."""
    print(f"\n  {BOLD}{prompt_text}{RESET}")
    for idx, opt in enumerate(options, 1):
        marker = f" {NEON_GREEN}◀{RESET}" if idx == default_val else ""
        print(f"    [{idx}] {opt}{marker}")
    
    while True:
        try:
            raw = input(f"  Enter choice [1-{len(options)}] (default: {default_val}): ").strip()
            if not raw:
                return default_val
            val = int(raw)
            if 1 <= val <= len(options):
                return val
        except (ValueError, EOFError):
            pass
        print(f"  Please enter a valid number between 1 and {len(options)}")

def prompt_input(prompt_text, default_val):
    """Prompts the operator for a text input with a default fallback."""
    raw = input(f"\n  {BOLD}{prompt_text}{RESET} [{default_val}]: ").strip()
    return raw if raw else default_val

def manage_visualization():
    clear_screen()
    print(f"  {BOLD}{NEON_CYAN}📊 GRAPH VISUALIZATION DASHBOARD MANAGER{RESET}")
    print(f"  {'━'*60}")
    print()
    
    # Check status
    neo4j_running = False
    server_running = False
    
    # Check neo4j docker container status
    try:
        r = subprocess.run(["docker", "ps", "--filter", "name=paper-processor-neo4j", "--format", "{{.Status}}"],
                           capture_output=True, text=True)
        if r.stdout.strip():
            neo4j_running = True
            neo4j_status = f"{NEON_GREEN}ACTIVE ({r.stdout.strip()}){RESET}"
        else:
            neo4j_status = f"{NEON_ORANGE}INACTIVE (Stopped){RESET}"
    except Exception:
        neo4j_status = f"{NEON_ORANGE}UNKNOWN (Docker error){RESET}"
        
    # Check if dashboard server is running on port 8585
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        if s.connect_ex(('localhost', 8585)) == 0:
            server_running = True
            server_status = f"{NEON_GREEN}ACTIVE (Listening on Port 8585){RESET}"
        else:
            server_status = f"{NEON_ORANGE}INACTIVE{RESET}"
            
    print(f"  • Neo4j Graph DB Container  : {neo4j_status}")
    print(f"  • Dashboard Web Server Port : {server_status}")
    print()
    
    action = prompt_choice("Select action", [
        "Start Visualization Services (Neo4j + Web Server)",
        "Stop Visualization Services",
        "Restart Services",
        "Create Native Database Snapshot (.dump)",
        "Return to Main Menu"
    ], default_val=1)
    
    if action == 1:
        print(f"\n  🔄 Starting services...")
        # Start Neo4j
        try:
            os.chdir("neo4j_viz")
            subprocess.run(["docker", "compose", "up", "-d"])
            os.chdir("..")
        except Exception as e:
            print(f"  ❌ Error starting Neo4j: {e}")
            os.chdir("..")
            
        # Start web server if not running
        if not server_running:
            try:
                subprocess.Popen([sys.executable, "neo4j_viz/server.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print("  🖥️   Spawning Dashboard Web Server in background...")
            except Exception as e:
                print(f"  ❌ Error spawning web server: {e}")
        print(f"\n  ✅ Services initialized! Access the dashboard at http://localhost:8585")
        time.sleep(2)
    elif action == 2:
        print(f"\n  🔄 Stopping services...")
        # Stop Neo4j
        try:
            os.chdir("neo4j_viz")
            subprocess.run(["docker", "compose", "down"])
            os.chdir("..")
        except Exception as e:
            print(f"  ❌ Error stopping Neo4j: {e}")
            os.chdir("..")
        # Stop web server
        try:
            subprocess.run("pkill -f 'neo4j_viz/server.py'", shell=True)
            print("  🛑 Stopped Dashboard Web Server process.")
        except Exception:
            pass
        print(f"\n  ✅ Services stopped successfully.")
        time.sleep(2)
    elif action == 3:
        print(f"\n  🔄 Restarting services...")
        # Stop first
        try:
            os.chdir("neo4j_viz")
            subprocess.run(["docker", "compose", "down"])
            os.chdir("..")
        except Exception:
            os.chdir("..")
        try:
            subprocess.run("pkill -f 'neo4j_viz/server.py'", shell=True)
        except Exception:
            pass
        # Start
        try:
            os.chdir("neo4j_viz")
            subprocess.run(["docker", "compose", "up", "-d"])
            os.chdir("..")
        except Exception:
            os.chdir("..")
        subprocess.Popen([sys.executable, "neo4j_viz/server.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"\n  ✅ Services restarted successfully!")
        time.sleep(2)
        
    elif action == 4:
        print(f"\n  🔄 Creating Native Database Snapshot...")
        try:
            subprocess.run(["bash", "snapshot_db.sh"])
        except Exception as e:
            print(f"  ❌ Error running snapshot script: {e}")
        input(f"\n  Press Enter to continue...")

    # Return to main menu recursively
    main()

def sync_graph_db():
    clear_screen()
    print(f"  {BOLD}{NEON_CYAN}🔄 MANUAL GRAPH DB SYNC{RESET}")
    print(f"  {'━'*60}")
    print()
    print("  Syncing processed papers in output directory to Neo4j database...")
    print("  This parses summaries, logic notation, theorems, and diagrams...")
    print()
    
    try:
        subprocess.run([sys.executable, "neo4j_viz/neo4j_importer.py"])
    except Exception as e:
        print(f"\n  ❌ Sync failed: {e}")
        
    input("\n  Press Enter to return to main menu...")
    main()

def launch_pipeline():
    clear_screen()
    print(f"  {BOLD}{NEON_CYAN}🦞 PIPELINE CONFIGURATION{RESET}")
    print(f"  {'━'*60}")
    
    # 1. Target Directory
    papers_dir = prompt_input("Target directory containing PDF papers", DEFAULT_PAPERS_DIR)
    while not os.path.exists(papers_dir):
        print(f"  ❌  Directory not found: {papers_dir}")
        papers_dir = prompt_input("Target directory containing PDF papers", DEFAULT_PAPERS_DIR)

    # 2. Execution Mode Selection
    mode = prompt_choice("Select GPU optimization strategy", [
        "Concurrent Zero-Swap Mode (RTX 5080 + RTX 3080 isolated residency)",
        "Standard Mode (Single-instance model scheduler)"
    ], default_val=1)

    primary_url = "http://localhost:11434"
    code_url = "http://localhost:11434"
    
    if mode == 1:
        # Prompt to spawn backend services
        spawn = prompt_input("Launch twin isolated Ollama instances on Port 11434 (GPU 0) & Port 11435 (GPU 1)? (y/n)", "y").lower()
        if spawn.startswith("y"):
            if os.path.exists(BACKEND_SCRIPT_PATH):
                print(f"\n  🔄 Starting isolated GPU backends via {BACKEND_SCRIPT_PATH}...")
                # Run the bash script as a detached background process
                subprocess.Popen(["bash", BACKEND_SCRIPT_PATH], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"  ⏳ Waiting 5 seconds for Ollama servers to initialize...")
                time.sleep(5)
                primary_url = "http://localhost:11434"
                code_url = "http://localhost:11435"
            else:
                print(f"  ⚠️  Startup script not found at {BACKEND_SCRIPT_PATH}. Please start endpoints manually.")
                primary_url = prompt_input("Primary Ollama URL", "http://localhost:11434")
                code_url = prompt_input("Code Ollama URL", "http://localhost:11435")
        else:
            primary_url = prompt_input("Primary Ollama URL", "http://localhost:11434")
            code_url = prompt_input("Code Ollama URL", "http://localhost:11435")
    else:
        primary_url = prompt_input("Ollama URL", "http://localhost:11434")
        code_url = primary_url

    # 3. Model Picking Mode
    picker_choice = prompt_choice("Model Selection Mode", [
        "Use optimized pinned concurrent defaults (DeepSeek-R1 14B & Qwen2.5-Coder 14B)",
        "Prompt for models interactively before running"
    ], default_val=1)

    # 4. Reprocessing Mode
    reprocess_choices = [
        "None (Process missing sections only)",
        "Summary",
        "Symbolic Logic",
        "C++ Examples",
        "Diagrams",
        "Extras",
        "All (Force re-run all sections)"
    ]
    reprocess_idx = prompt_choice("Do you want to re-run any specific section?", reprocess_choices, default_val=1)

    # 5. Workers
    workers = prompt_input("Number of parallel paper workers (VRAM permitting)", "1")

    # 6. Target Paper
    paper_scope = prompt_choice("Paper Processing Scope", [
        "Process all PDF papers in the target directory",
        "Process a single paper by filename"
    ], default_val=1)

    single_paper = None
    if paper_scope == 2:
        single_paper = prompt_input("Enter PDF filename (e.g. attention.pdf)", "")
        while not single_paper:
            print("  ❌ Filename cannot be empty.")
            single_paper = prompt_input("Enter PDF filename (e.g. attention.pdf)", "")

    # Build the final command
    cmd = ["python3", FORK_SCRIPT_PATH, papers_dir]
    
    if primary_url:
        cmd.extend(["--primary-url", primary_url])
    if code_url:
        cmd.extend(["--code-url", code_url])
    
    if picker_choice == 2:
        cmd.append("-s")
        cmd.append("-c")

    if reprocess_idx > 1:
        sec = reprocess_choices[reprocess_idx - 1].split(" ")[0].lower()
        cmd.extend(["--reprocess", sec])

    if workers != "1":
        cmd.extend(["--workers", workers])

    if single_paper:
        cmd.extend(["--paper", single_paper])

    # Show review screen
    clear_screen()
    print(f"  {BOLD}{NEON_CYAN}🦞 PIPELINE CONFIGURATION REVIEW{RESET}")
    print(f"  {'━'*60}")
    print(f"  Papers Directory : {papers_dir}")
    print(f"  Primary Ollama   : {primary_url}")
    print(f"  Code Ollama      : {code_url}")
    print(f"  Scope            : {'All papers' if not single_paper else f'Single paper: {single_paper}'}")
    print(f"  Workers          : {workers}")
    print(f"  Reprocessing     : {reprocess_choices[reprocess_idx - 1]}")
    print()
    print(f"  {BOLD}Command to execute:{RESET}")
    cmd_str = " ".join(cmd)
    print(f"    {NEON_GREEN}{cmd_str}{RESET}")
    print(f"  {'━'*60}")

    confirm = prompt_input("Press Enter to launch pipeline (or 'c' to cancel)", "").lower()
    if confirm.startswith("c"):
        print("\n  ❌ Launch cancelled by operator.")
        time.sleep(1)
        main()
        return

    print(f"\n  🚀 Launching pipeline process...\n")
    
    # os.execvp will replace the current process image with the new script
    os.execvp("python3", cmd)

def main():
    clear_screen()
    print(f"  {BOLD}{NEON_CYAN}🦞 DUAL-GPU VRAM-RESIDENT CONTROL CENTER{RESET}")
    print(f"  {'━'*60}")
    print(f"  Manage the dual-GPU zero-swap resident pipeline and graph dashboard.")
    print()

    choice = prompt_choice("Select operation", [
        "Launch Paper Processing Pipeline",
        "Manage Graph Visualization Dashboard (Neo4j + Web Server)",
        "Sync Processed Papers to Neo4j Graph DB",
        "Exit to Shell"
    ], default_val=1)

    if choice == 1:
        launch_pipeline()
    elif choice == 2:
        manage_visualization()
    elif choice == 3:
        sync_graph_db()
    else:
        print("\n  👋 Exited. Have a splendid session!")
        sys.exit(0)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import os
import sys
import time
import json
import subprocess
import urllib.request
import urllib.error

# ANSI Color Codes for premium terminal output
NEON_GREEN = "\033[38;2;0;255;65m"
NEON_MAGENTA = "\033[38;2;255;0;255m"
NEON_CYAN = "\033[38;2;0;255;255m"
NEON_YELLOW = "\033[38;2;255;255;0m"
NEON_ORANGE = "\033[38;2;255;102;0m"
RESET = "\033[0m"
BOLD = "\033[1m"
UNDERLINE = "\033[4m"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

def get_gpu_telemetry():
    """Queries GPU metrics using nvidia-smi command-line utility."""
    cmd = [
        "nvidia-smi",
        "--query-gpu=index,name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode != 0:
            return None
        gpus = []
        for line in res.stdout.strip().split("\n"):
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            gpus.append({
                "index": int(parts[0]),
                "name": parts[1],
                "gpu_util": float(parts[2]),
                "mem_util": float(parts[3]),
                "mem_used_mib": float(parts[4]),
                "mem_total_mib": float(parts[5]),
                "temp_c": float(parts[6]),
                "power_w": float(parts[7])
            })
        return gpus
    except Exception:
        return None

def get_cpu_times():
    """Reads system CPU stats from /proc/stat."""
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        parts = line.split()
        if parts[0] == "cpu":
            # user, nice, system, idle, iowait, irq, softirq
            times = [float(x) for x in parts[1:8]]
            idle = times[3] + times[4]  # idle + iowait
            total = sum(times)
            return idle, total
    except Exception:
        pass
    return 0, 0

def get_ram_telemetry():
    """Reads memory utilization from /proc/meminfo."""
    try:
        mem_total = 0
        mem_avail = 0
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1]) * 1024 # to bytes
                elif line.startswith("MemAvailable:"):
                    mem_avail = int(line.split()[1]) * 1024
        if mem_total > 0:
            mem_used = mem_total - mem_avail
            return mem_used, mem_total
    except Exception:
        pass
    return 0, 0

def get_disk_telemetry():
    """Reads sector read/write metrics from /proc/diskstats."""
    try:
        read_sectors = 0
        write_sectors = 0
        with open("/proc/diskstats") as f:
            for line in f:
                parts = line.split()
                # Skip loop devices and ram devices
                if len(parts) >= 13 and not parts[2].startswith("loop") and not parts[2].startswith("ram"):
                    read_sectors += int(parts[5])
                    write_sectors += int(parts[9])
        # Sector size is typically 512 bytes
        return read_sectors * 512, write_sectors * 512
    except Exception:
        pass
    return 0, 0

def get_ollama_models():
    """Queries resident models from Ollama API."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/ps")
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode())
            return data.get("models", [])
    except Exception:
        return []

def draw_bar(percentage, length=20):
    """Draws a visual progress bar representing resource utilization."""
    filled = int(round(length * (percentage / 100.0)))
    bar = "█" * filled + "░" * (length - filled)
    return bar

def clear_screen():
    """Clears the terminal screen."""
    print("\033[H\033[J", end="")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Live zero-dependency GPU & System Telemetry Monitor for Ollama pipeline.")
    parser.add_argument("--interval", "-i", type=float, default=1.0, help="Sampling interval in seconds")
    parser.add_argument("--output", "-o", type=str, default=None, help="Path to write JSONL logs to")
    parser.add_argument("--once", action="store_true", help="Take a single snapshot and exit")
    args = parser.parse_args()

    # Pre-sampling for rates calculation
    cpu_idle1, cpu_total1 = get_cpu_times()
    disk_read1, disk_write1 = get_disk_telemetry()
    time1 = time.time()
    if args.once:
        time.sleep(0.5)  # sleep briefly to compute deltas for CPU/Disk utilization

    # Create directory for output if specified
    out_file = None
    if args.output:
        out_path = os.path.abspath(args.output)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        out_file = open(out_path, "a", encoding="utf-8")
        print(f"Logging telemetry to: {out_path}")
        time.sleep(1)

    if not args.once:
        print("Starting Telemetry Monitor... Press Ctrl+C to stop.")
        time.sleep(0.5)

    try:
        first_run = True
        while True:
            if not args.once and not first_run:
                time.sleep(args.interval)
            first_run = False
            
            # Gather current metrics
            time2 = time.time()
            dt = time2 - time1
            time1 = time2

            # GPU
            gpus = get_gpu_telemetry()
            
            # CPU
            cpu_idle2, cpu_total2 = get_cpu_times()
            idle_d = cpu_idle2 - cpu_idle1
            total_d = cpu_total2 - cpu_total1
            cpu_util = (1.0 - (idle_d / (total_d or 1.0))) * 100.0
            cpu_idle1, cpu_total1 = cpu_idle2, cpu_total2
            
            # RAM
            ram_used, ram_total = get_ram_telemetry()
            
            # Disk
            disk_read2, disk_write2 = get_disk_telemetry()
            read_bps = (disk_read2 - disk_read1) / dt
            write_bps = (disk_write2 - disk_write1) / dt
            disk_read1, disk_write1 = disk_read2, disk_write2
            
            # Ollama
            ollama_models = get_ollama_models()

            # Record telemetry data log if output is set
            log_entry = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "cpu_util_pct": cpu_util,
                "ram_used_gb": ram_used / (1024**3),
                "ram_total_gb": ram_total / (1024**3),
                "disk_read_mbps": read_bps / (1024**2),
                "disk_write_mbps": write_bps / (1024**2),
                "gpus": gpus,
                "ollama_models": [
                    {
                        "name": m["name"],
                        "size_bytes": m.get("size"),
                        "vram_bytes": m.get("size_vram"),
                    } for m in ollama_models
                ]
            }
            if out_file:
                out_file.write(json.dumps(log_entry) + "\n")
                out_file.flush()

            # Draw Terminal Dashboard
            clear_screen()
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"  {BOLD}{NEON_CYAN}SYSTEM TELEMETRY MONITOR  [{now_str}]{RESET}")
            print(f"  {'─'*78}")
            
            if gpus:
                for gpu in gpus:
                    idx = gpu["index"]
                    name = gpu["name"]
                    util = gpu["gpu_util"]
                    mem_used = gpu["mem_used_mib"] / 1024.0
                    mem_total = gpu["mem_total_mib"] / 1024.0
                    mem_util_pct = (gpu["mem_used_mib"] / gpu["mem_total_mib"]) * 100.0
                    temp = gpu["temp_c"]
                    power = gpu["power_w"]
                    
                    # Highlight colors depending on load
                    util_color = NEON_GREEN if util < 50 else (NEON_YELLOW if util < 85 else NEON_ORANGE)
                    vram_color = NEON_GREEN if mem_util_pct < 80 else NEON_MAGENTA
                    
                    print(f"  {BOLD}GPU {idx}: {name}{RESET}  | Temp: {temp}°C | Power: {power}W")
                    print(f"  [{util_color}{draw_bar(util)}{RESET}]  {BOLD}{util:>3.0f}% Util{RESET}")
                    print(f"  [{vram_color}{draw_bar(mem_util_pct)}{RESET}]  {BOLD}VRAM: {mem_used:.1f} / {mem_total:.1f} GiB ({mem_util_pct:.0f}%){RESET}")
                    print()
            else:
                print("  ⚠️   No GPU detected or nvidia-smi failed.")
                print()

            print(f"  {'─'*78}")
            print(f"  {BOLD}SYSTEM METRICS{RESET}")
            ram_used_gb = ram_used / (1024**3)
            ram_total_gb = ram_total / (1024**3)
            ram_pct = (ram_used / ram_total * 100) if ram_total > 0 else 0
            
            print(f"  CPU Utilization : {NEON_GREEN}{cpu_util:>5.1f}%{RESET}  | Disk Read  : {NEON_CYAN}{read_bps / (1024**2):>6.1f} MB/s{RESET}")
            print(f"  System RAM      : {NEON_GREEN}{ram_used_gb:>5.1f} / {ram_total_gb:.1f} GiB ({ram_pct:.0f}%){RESET} | Disk Write : {NEON_CYAN}{write_bps / (1024**2):>6.1f} MB/s{RESET}")
            print()
            
            print(f"  {'─'*78}")
            print(f"  {BOLD}OLLAMA VRAM RESIDENCY{RESET}")
            if ollama_models:
                for model in ollama_models:
                    m_name = model["name"]
                    m_size_gb = model.get("size", 0) / (1024**3)
                    m_vram_gb = model.get("size_vram", 0) / (1024**3)
                    print(f"  • {NEON_MAGENTA}{m_name:<40}{RESET} Size: {m_size_gb:>5.2f} GB  |  VRAM: {NEON_GREEN}{m_vram_gb:>5.2f} GB{RESET}")
            else:
                print("  • No resident models in VRAM (Ollama is idle)")
            print(f"  {'─'*78}")
            if args.once:
                break

    except KeyboardInterrupt:
        print("\nExiting monitor...")
    finally:
        if out_file:
            out_file.close()

if __name__ == "__main__":
    main()

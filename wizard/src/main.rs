//! paper-wizard — Ratatui TUI for the paper_processor.py pipeline.
//!
//! Tabs: Overview · Scan · Config · Run · Help
//! Key: Tab / Shift-Tab switch tabs, q quits, ? help.

use anyhow::Result;
use crossterm::{
    event::{
        self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyEvent, KeyEventKind,
        KeyModifiers,
    },
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{
    layout::{Alignment, Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{
        Block, BorderType, Borders, Clear, Gauge, List, ListItem, ListState, Paragraph, Tabs, Wrap,
    },
    Frame, Terminal,
};
use serde::Deserialize;
use std::{
    io::{self, BufRead, BufReader},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        mpsc::{self, Receiver, Sender, TryRecvError},
        Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};
use walkdir::WalkDir;

// ──────────────────────────────────────────────────────────────────────
// Theme — neon-on-black to match the pipeline's diagram aesthetic
// ──────────────────────────────────────────────────────────────────────
const NEON_GREEN: Color = Color::Rgb(0x00, 0xFF, 0x41);
const NEON_MAGENTA: Color = Color::Rgb(0xFF, 0x00, 0xFF);
const NEON_CYAN: Color = Color::Rgb(0x00, 0xFF, 0xFF);
const NEON_ORANGE: Color = Color::Rgb(0xFF, 0x66, 0x00);
const NEON_YELLOW: Color = Color::Rgb(0xFF, 0xFF, 0x00);
const DIM_GREY: Color = Color::Rgb(0x55, 0x55, 0x55);

const PROCESSOR_PY: &str = "paper_processor.py";
fn default_papers_dir() -> String {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    format!("{}/Documents/AI-ML_Papers", home)
}
const VENV_PYTHON: &str = "/home/jeb/programs/python_programs/venv/bin/python";
const OLLAMA_URL: &str = "http://localhost:11434";
const TAB_TITLES: &[&str] = &["Overview", "Scan", "Config", "Run", "Help"];

// ──────────────────────────────────────────────────────────────────────
// Known-good model lists (mirrors paper_processor.py KNOWN_GOOD_* lists)
// ──────────────────────────────────────────────────────────────────────
struct ModelEntry {
    name: &'static str,
    vram: &'static str,
    role: &'static str,
}

const KNOWN_GOOD_MODELS: &[ModelEntry] = &[
    ModelEntry { name: "nemotron-3-nano-30b-small:latest",   vram: "~24 GB", role: "xl_quality — current default" },
    ModelEntry { name: "deepseek-r1:32b",                    vram: "~19 GB", role: "xl_reason — chain-of-thought" },
    ModelEntry { name: "deepseek-r1:14b-qwen-distill-q8_0", vram: "~15 GB", role: "mid_reason — Q8 fidelity" },
    ModelEntry { name: "devstral:24b",                       vram: "~14 GB", role: "mid_code — code + text" },
    ModelEntry { name: "qwen3.6:35b",                        vram: "~23 GB", role: "xl — strong general reasoning" },
    ModelEntry { name: "deepseek-r1:14b",                    vram: "~9 GB",  role: "single — reliable, 9 GB" },
    ModelEntry { name: "gpt-oss:20b",                        vram: "~13 GB", role: "text-only alternative" },
    ModelEntry { name: "deepseek-r1:8b",                     vram: "~5 GB",  role: "fast — quick fallback" },
];

const KNOWN_GOOD_CODE_MODELS: &[ModelEntry] = &[
    ModelEntry { name: "qwen3-coder:30b",              vram: "~18 GB", role: "xl_code — current default, best C++" },
    ModelEntry { name: "devstral:24b",                 vram: "~14 GB", role: "mid_code — strong code, lower VRAM" },
    ModelEntry { name: "qwen2.5-coder:14b-base-q6_K", vram: "~12 GB", role: "single — Q6 fidelity" },
    ModelEntry { name: "qwen2.5-coder:14b",           vram: "~9 GB",  role: "single — reliable, single-GPU" },
    ModelEntry { name: "deepseek-coder-v2:16b",       vram: "~9 GB",  role: "single — fast alternative" },
    ModelEntry { name: "qwen2.5-coder:7b",            vram: "~5 GB",  role: "fast — quick fallback" },
];

// ──────────────────────────────────────────────────────────────────────
// App state
// ──────────────────────────────────────────────────────────────────────
#[derive(Deserialize)]
struct Metadata {
    model_used: Option<String>,
    sections_completed: Option<Vec<String>>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Status {
    NotStarted,
    Partial,
    Complete,
}

struct PaperEntry {
    rel_path: String,
    status: Status,
    model: Option<String>,
}

struct ScanResult {
    entries: Vec<PaperEntry>,
    complete: usize,
    partial: usize,
    pending: usize,
}

#[derive(PartialEq, Eq)]
enum Backend {
    Ollama,
    Openclaw,
}
impl Backend {
    fn as_str(&self) -> &'static str {
        match self {
            Backend::Ollama => "ollama",
            Backend::Openclaw => "openclaw",
        }
    }
}

struct Config {
    papers_dir:          String,
    backend:             Backend,
    model_override:      String, // empty = auto-select by page count
    code_model_override: String, // empty = use CODE_MODEL default (qwen3-coder:30b)
    single_paper:        String, // empty = all
    reprocess:           String, // empty | summary|logic|cpp|diagrams|extras|all
    workers:             u32,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            papers_dir:          default_papers_dir(),
            backend:             Backend::Ollama,
            model_override:      String::new(),
            code_model_override: String::new(),
            single_paper:        String::new(),
            reprocess:           String::new(),
            workers:             1,
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum ConfigField {
    PapersDir,
    Backend,
    Model,
    CodeModel,
    Paper,
    Reprocess,
    Workers,
}
impl ConfigField {
    fn next(self) -> Self {
        match self {
            ConfigField::PapersDir  => ConfigField::Backend,
            ConfigField::Backend    => ConfigField::Model,
            ConfigField::Model      => ConfigField::CodeModel,
            ConfigField::CodeModel  => ConfigField::Paper,
            ConfigField::Paper      => ConfigField::Reprocess,
            ConfigField::Reprocess  => ConfigField::Workers,
            ConfigField::Workers    => ConfigField::PapersDir,
        }
    }
}

struct RunState {
    child: Option<Child>,
    rx: Option<Receiver<String>>,
    log: Vec<String>,
    scroll: u16,
    autoscroll: bool,
    started: Option<Instant>,
    finished: bool,
    exit_code: Option<i32>,
    current_paper: String,
    current_section: String,
    papers_done: usize,
}

impl Default for RunState {
    fn default() -> Self {
        Self {
            child: None,
            rx: None,
            log: Vec::new(),
            scroll: 0,
            autoscroll: true,
            started: None,
            finished: false,
            exit_code: None,
            current_paper: String::new(),
            current_section: String::new(),
            papers_done: 0,
        }
    }
}

struct Health {
    ollama_ok: bool,
    ollama_models: usize,
    graphviz_ok: bool,
    gpu_info: String,
    python_ok: bool,
    last_checked: Option<Instant>,
}

impl Default for Health {
    fn default() -> Self {
        Self {
            ollama_ok: false,
            ollama_models: 0,
            graphviz_ok: false,
            gpu_info: String::from("(not probed yet)"),
            python_ok: false,
            last_checked: None,
        }
    }
}

struct App {
    tab: usize,
    cfg: Config,
    field: ConfigField,
    scan: Option<ScanResult>,
    scan_state: ListState,
    scanning: bool,
    scan_rx: Option<Receiver<ScanResult>>,
    run: Arc<Mutex<RunState>>,
    health: Health,
    should_quit: bool,
    show_help_overlay:  bool,
    show_model_picker:  bool,
    show_code_picker:   bool,
    picker_idx:         usize,
    status_msg: String,
}

impl App {
    fn new() -> Self {
        let mut scan_state = ListState::default();
        scan_state.select(Some(0));
        Self {
            tab: 0,
            cfg: Config::default(),
            field: ConfigField::PapersDir,
            scan: None,
            scan_state,
            scanning: false,
            scan_rx: None,
            run: Arc::new(Mutex::new(RunState::default())),
            health: Health::default(),
            should_quit: false,
            show_help_overlay: false,
            show_model_picker: false,
            show_code_picker:  false,
            picker_idx:        0,
            status_msg: String::from("Welcome. Press ? for help, Tab to switch panels."),
        }
    }
}

// ──────────────────────────────────────────────────────────────────────
// Main
// ──────────────────────────────────────────────────────────────────────
fn main() -> Result<()> {
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
    let backend = ratatui::backend::CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let mut app = App::new();
    probe_health(&mut app.health);

    let res = run_app(&mut terminal, &mut app);

    disable_raw_mode()?;
    execute!(
        terminal.backend_mut(),
        LeaveAlternateScreen,
        DisableMouseCapture
    )?;
    terminal.show_cursor()?;

    res
}

fn run_app<B: ratatui::backend::Backend>(
    terminal: &mut Terminal<B>,
    app: &mut App,
) -> Result<()> {
    let tick = Duration::from_millis(120);
    let mut last_tick = Instant::now();
    loop {
        terminal.draw(|f| draw(f, app))?;

        let timeout = tick.saturating_sub(last_tick.elapsed());
        if event::poll(timeout)? {
            if let Event::Key(k) = event::read()? {
                if k.kind == KeyEventKind::Press {
                    handle_key(app, k)?;
                }
            }
        }
        if last_tick.elapsed() >= tick {
            on_tick(app);
            last_tick = Instant::now();
        }
        if app.should_quit {
            // Kill child if running
            let mut rs = app.run.lock().unwrap();
            if let Some(child) = rs.child.as_mut() {
                let _ = child.kill();
            }
            return Ok(());
        }
    }
}

// ──────────────────────────────────────────────────────────────────────
// Tick — drain async results
// ──────────────────────────────────────────────────────────────────────
fn on_tick(app: &mut App) {
    // Scan completion
    if let Some(rx) = app.scan_rx.as_ref() {
        match rx.try_recv() {
            Ok(sr) => {
                app.scan = Some(sr);
                app.scanning = false;
                app.scan_rx = None;
                app.status_msg = format!(
                    "Scan complete: {} papers",
                    app.scan.as_ref().unwrap().entries.len()
                );
            }
            Err(TryRecvError::Empty) => {}
            Err(TryRecvError::Disconnected) => {
                app.scanning = false;
                app.scan_rx = None;
            }
        }
    }

    // Child log pump + exit polling
    let mut rs = app.run.lock().unwrap();
    // Drain the channel without holding an immutable borrow on rs.
    let mut drained: Vec<String> = Vec::new();
    if let Some(rx) = rs.rx.take() {
        for _ in 0..256 {
            match rx.try_recv() {
                Ok(line) => drained.push(line),
                Err(_) => break,
            }
        }
        rs.rx = Some(rx);
    }
    for line in drained {
        update_progress_from_line(&mut rs, &line);
        rs.log.push(line);
    }
    if let Some(child) = rs.child.as_mut() {
        match child.try_wait() {
            Ok(Some(status)) => {
                rs.finished = true;
                rs.exit_code = status.code();
                rs.child = None;
            }
            Ok(None) => {}
            Err(_) => {}
        }
    }
}

fn update_progress_from_line(rs: &mut RunState, line: &str) {
    let t = line.trim_start();
    if t.starts_with("📄  ") {
        rs.current_paper = t.trim_start_matches("📄  ").to_string();
        rs.current_section.clear();
    } else if t.starts_with("📝") {
        rs.current_section = "summary".into();
    } else if t.starts_with("🔣") {
        rs.current_section = "logic".into();
    } else if t.starts_with("💻") {
        rs.current_section = "cpp".into();
    } else if t.starts_with("📊") {
        rs.current_section = "diagrams".into();
    } else if t.starts_with("💡") {
        rs.current_section = "extras".into();
    } else if t.starts_with("✅  Output →") {
        rs.papers_done += 1;
        rs.current_section = "done".into();
    }
}

// ──────────────────────────────────────────────────────────────────────
// Key handling
// ──────────────────────────────────────────────────────────────────────
fn handle_key(app: &mut App, k: KeyEvent) -> Result<()> {
    if app.show_help_overlay {
        app.show_help_overlay = false;
        return Ok(());
    }

    if app.show_model_picker || app.show_code_picker {
        handle_picker_key(app, k);
        return Ok(());
    }

    // Global
    match (k.code, k.modifiers) {
        (KeyCode::Char('q'), _) | (KeyCode::Char('c'), KeyModifiers::CONTROL) => {
            app.should_quit = true;
            return Ok(());
        }
        (KeyCode::Char('?'), _) => {
            app.show_help_overlay = true;
            return Ok(());
        }
        (KeyCode::Tab, _) => {
            app.tab = (app.tab + 1) % TAB_TITLES.len();
            return Ok(());
        }
        (KeyCode::BackTab, _) => {
            app.tab = (app.tab + TAB_TITLES.len() - 1) % TAB_TITLES.len();
            return Ok(());
        }
        (KeyCode::F(5), _) => {
            probe_health(&mut app.health);
            app.status_msg = "Health re-probed.".into();
            return Ok(());
        }
        _ => {}
    }

    match app.tab {
        1 => handle_scan_key(app, k),
        2 => handle_config_key(app, k),
        3 => handle_run_key(app, k),
        _ => {}
    }
    Ok(())
}

fn handle_scan_key(app: &mut App, k: KeyEvent) {
    match k.code {
        KeyCode::Char('s') => start_scan(app),
        KeyCode::Down | KeyCode::Char('j') => {
            if let Some(sr) = &app.scan {
                let i = app.scan_state.selected().unwrap_or(0);
                let ni = (i + 1).min(sr.entries.len().saturating_sub(1));
                app.scan_state.select(Some(ni));
            }
        }
        KeyCode::Up | KeyCode::Char('k') => {
            let i = app.scan_state.selected().unwrap_or(0);
            app.scan_state.select(Some(i.saturating_sub(1)));
        }
        KeyCode::PageDown => {
            if let Some(sr) = &app.scan {
                let i = app.scan_state.selected().unwrap_or(0);
                let ni = (i + 20).min(sr.entries.len().saturating_sub(1));
                app.scan_state.select(Some(ni));
            }
        }
        KeyCode::PageUp => {
            let i = app.scan_state.selected().unwrap_or(0);
            app.scan_state.select(Some(i.saturating_sub(20)));
        }
        KeyCode::Enter => {
            if let Some(sr) = &app.scan {
                if let Some(i) = app.scan_state.selected() {
                    if let Some(e) = sr.entries.get(i) {
                        app.cfg.single_paper = e.rel_path.clone();
                        app.tab = 2;
                        app.field = ConfigField::Paper;
                        app.status_msg =
                            format!("Selected '{}' → jump to Config, then Run.", e.rel_path);
                    }
                }
            }
        }
        _ => {}
    }
}

fn handle_config_key(app: &mut App, k: KeyEvent) {
    match k.code {
        KeyCode::Down | KeyCode::Char('j') => app.field = app.field.next(),
        KeyCode::Up | KeyCode::Char('k') => {
            // previous: cycle forward (n_fields - 1) times
            let mut f = app.field;
            for _ in 0..6 {
                f = f.next();
            }
            app.field = f;
        }
        KeyCode::Left | KeyCode::Right => cycle_field_value(app, k.code == KeyCode::Right),
        KeyCode::Backspace => pop_field_char(app),
        KeyCode::Char('i') => {
            if app.field == ConfigField::Model {
                app.show_model_picker = true;
                app.picker_idx = 0;
                app.status_msg = "Model picker — ↑/↓ navigate · Enter select · Esc cancel".into();
            } else if app.field == ConfigField::CodeModel {
                app.show_code_picker = true;
                app.picker_idx = 0;
                app.status_msg = "Code model picker — ↑/↓ navigate · Enter select · Esc cancel".into();
            } else {
                push_field_char(app, 'i');
            }
        }
        KeyCode::Char(c) => {
            push_field_char(app, c);
        }
        KeyCode::Enter => {
            app.tab = 3;
            app.status_msg = "Switched to Run tab. Press L to launch.".into();
        }
        _ => {}
    }
}

fn handle_picker_key(app: &mut App, k: KeyEvent) {
    let list: &[ModelEntry] = if app.show_model_picker {
        KNOWN_GOOD_MODELS
    } else {
        KNOWN_GOOD_CODE_MODELS
    };
    match k.code {
        KeyCode::Up | KeyCode::Char('k') => {
            app.picker_idx = app.picker_idx.saturating_sub(1);
        }
        KeyCode::Down | KeyCode::Char('j') => {
            app.picker_idx = (app.picker_idx + 1).min(list.len().saturating_sub(1));
        }
        KeyCode::Enter => {
            let chosen = list[app.picker_idx].name.to_string();
            if app.show_model_picker {
                app.cfg.model_override = chosen;
                app.status_msg = format!("Main model set to {}", app.cfg.model_override);
            } else {
                app.cfg.code_model_override = chosen;
                app.status_msg = format!("Code model set to {}", app.cfg.code_model_override);
            }
            app.show_model_picker = false;
            app.show_code_picker  = false;
        }
        KeyCode::Esc | KeyCode::Char('q') => {
            app.show_model_picker = false;
            app.show_code_picker  = false;
            app.status_msg = "Picker dismissed.".into();
        }
        _ => {}
    }
}

fn cycle_field_value(app: &mut App, forward: bool) {
    match app.field {
        ConfigField::Backend => {
            app.cfg.backend = match app.cfg.backend {
                Backend::Ollama => Backend::Openclaw,
                Backend::Openclaw => Backend::Ollama,
            };
            let _ = forward;
        }
        ConfigField::Reprocess => {
            let opts = ["", "summary", "logic", "cpp", "diagrams", "extras", "all"];
            let cur = opts
                .iter()
                .position(|s| *s == app.cfg.reprocess)
                .unwrap_or(0);
            let ni = if forward {
                (cur + 1) % opts.len()
            } else {
                (cur + opts.len() - 1) % opts.len()
            };
            app.cfg.reprocess = opts[ni].to_string();
        }
        ConfigField::Workers => {
            if forward {
                app.cfg.workers = (app.cfg.workers + 1).min(8);
            } else {
                app.cfg.workers = app.cfg.workers.saturating_sub(1).max(1);
            }
        }
        _ => {}
    }
}

fn push_field_char(app: &mut App, c: char) {
    let target: Option<&mut String> = match app.field {
        ConfigField::PapersDir  => Some(&mut app.cfg.papers_dir),
        ConfigField::Model      => Some(&mut app.cfg.model_override),
        ConfigField::CodeModel  => Some(&mut app.cfg.code_model_override),
        ConfigField::Paper      => Some(&mut app.cfg.single_paper),
        _ => None,
    };
    if let Some(s) = target {
        s.push(c);
    }
}

fn pop_field_char(app: &mut App) {
    let target: Option<&mut String> = match app.field {
        ConfigField::PapersDir  => Some(&mut app.cfg.papers_dir),
        ConfigField::Model      => Some(&mut app.cfg.model_override),
        ConfigField::CodeModel  => Some(&mut app.cfg.code_model_override),
        ConfigField::Paper      => Some(&mut app.cfg.single_paper),
        _ => None,
    };
    if let Some(s) = target {
        s.pop();
    }
}

fn handle_run_key(app: &mut App, k: KeyEvent) {
    match k.code {
        KeyCode::Char('l') | KeyCode::Char('L') => launch_processor(app),
        KeyCode::Char('x') | KeyCode::Char('X') => kill_running(app),
        KeyCode::Char('c') => clear_log(app),
        KeyCode::Up | KeyCode::Char('k') => {
            let mut rs = app.run.lock().unwrap();
            rs.scroll = rs.scroll.saturating_sub(1);
            rs.autoscroll = false;
        }
        KeyCode::Down | KeyCode::Char('j') => {
            let mut rs = app.run.lock().unwrap();
            rs.scroll = rs.scroll.saturating_add(1);
            rs.autoscroll = false;
        }
        KeyCode::PageUp => {
            let mut rs = app.run.lock().unwrap();
            rs.scroll = rs.scroll.saturating_sub(20);
            rs.autoscroll = false;
        }
        KeyCode::PageDown => {
            let mut rs = app.run.lock().unwrap();
            rs.scroll = rs.scroll.saturating_add(20);
            rs.autoscroll = false;
        }
        KeyCode::End => {
            let mut rs = app.run.lock().unwrap();
            rs.autoscroll = true;
        }
        _ => {}
    }
}

// ──────────────────────────────────────────────────────────────────────
// Actions — scan, launch, health
// ──────────────────────────────────────────────────────────────────────
fn start_scan(app: &mut App) {
    if app.scanning {
        app.status_msg = "Scan already in progress…".into();
        return;
    }
    let dir = PathBuf::from(&app.cfg.papers_dir);
    if !dir.exists() {
        app.status_msg = format!("Directory not found: {}", dir.display());
        return;
    }
    let (tx, rx) = mpsc::channel();
    app.scan_rx = Some(rx);
    app.scanning = true;
    app.status_msg = "Scanning PDFs recursively…".into();
    thread::spawn(move || {
        let result = scan_papers(&dir);
        let _ = tx.send(result);
    });
}

fn scan_papers(dir: &Path) -> ScanResult {
    let processed_root = dir.join("_processed");
    let mut entries = Vec::new();
    let (mut complete, mut partial, mut pending) = (0usize, 0usize, 0usize);

    for ent in WalkDir::new(dir).into_iter().filter_map(|e| e.ok()) {
        let p = ent.path();
        if !ent.file_type().is_file() {
            continue;
        }
        if p.extension().and_then(|e| e.to_str()).map(|s| s.to_lowercase()) != Some("pdf".into()) {
            continue;
        }
        // Skip anything under _processed
        if p.starts_with(&processed_root) {
            continue;
        }

        let rel = p.strip_prefix(dir).unwrap_or(p).to_string_lossy().to_string();
        let meta_path = metadata_path_for(&processed_root, dir, p);
        let (status, model) = classify(&meta_path);
        match status {
            Status::Complete => complete += 1,
            Status::Partial => partial += 1,
            Status::NotStarted => pending += 1,
        }
        entries.push(PaperEntry {
            rel_path: rel,
            status,
            model,
        });
    }
    entries.sort_by(|a, b| a.rel_path.cmp(&b.rel_path));
    ScanResult {
        entries,
        complete,
        partial,
        pending,
    }
}

fn slugify(s: &str) -> String {
    let mut out: String = s
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c.to_ascii_lowercase()
            } else {
                '_'
            }
        })
        .collect();
    // strip leading/trailing underscores
    while out.starts_with('_') {
        out.remove(0);
    }
    while out.ends_with('_') {
        out.pop();
    }
    if out.len() > 64 {
        out.truncate(64);
    }
    out
}

fn metadata_path_for(processed_root: &Path, papers_dir: &Path, pdf: &Path) -> PathBuf {
    let mut p = processed_root.to_path_buf();
    if let Ok(rel_parent) = pdf.parent().unwrap_or(papers_dir).strip_prefix(papers_dir) {
        for part in rel_parent.iter() {
            p.push(slugify(&part.to_string_lossy()));
        }
    }
    let stem = pdf
        .file_stem()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_default();
    p.push(slugify(&stem));
    p.push("metadata.json");
    p
}

fn classify(meta_path: &Path) -> (Status, Option<String>) {
    if !meta_path.exists() {
        return (Status::NotStarted, None);
    }
    let data = std::fs::read_to_string(meta_path).unwrap_or_default();
    let meta: Result<Metadata, _> = serde_json::from_str(&data);
    match meta {
        Ok(m) => {
            let completed: Vec<String> = m.sections_completed.unwrap_or_default();
            let all = ["summary", "logic", "cpp", "diagrams", "extras"];
            let done = all.iter().all(|s| completed.iter().any(|c| c == s));
            if done {
                (Status::Complete, m.model_used)
            } else {
                (Status::Partial, m.model_used)
            }
        }
        Err(_) => (Status::Partial, None),
    }
}

fn launch_processor(app: &mut App) {
    let mut rs = app.run.lock().unwrap();
    if rs.child.is_some() {
        app.status_msg = "Already running. Press X to kill.".into();
        return;
    }
    rs.log.clear();
    rs.scroll = 0;
    rs.autoscroll = true;
    rs.started = Some(Instant::now());
    rs.finished = false;
    rs.exit_code = None;
    rs.current_paper.clear();
    rs.current_section.clear();
    rs.papers_done = 0;

    let script = find_processor_script();
    let python = if Path::new(VENV_PYTHON).exists() {
        VENV_PYTHON.to_string()
    } else {
        "python3".into()
    };

    let mut cmd = Command::new(&python);
    cmd.arg(&script).arg(&app.cfg.papers_dir);
    cmd.arg("--backend").arg(app.cfg.backend.as_str());
    if !app.cfg.model_override.trim().is_empty() {
        cmd.arg("--model").arg(app.cfg.model_override.trim());
    }
    if !app.cfg.code_model_override.trim().is_empty() {
        cmd.arg("--code-model").arg(app.cfg.code_model_override.trim());
    }
    if !app.cfg.single_paper.trim().is_empty() {
        cmd.arg("--paper").arg(app.cfg.single_paper.trim());
    }
    if !app.cfg.reprocess.trim().is_empty() {
        cmd.arg("--reprocess").arg(app.cfg.reprocess.trim());
    }
    if app.cfg.workers > 1 {
        cmd.arg("--workers").arg(app.cfg.workers.to_string());
    }
    cmd.env("PYTHONUNBUFFERED", "1");
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());

    let cmdline = format!("{:?}", cmd);
    rs.log.push(format!("$ {}", cmdline));

    match cmd.spawn() {
        Ok(mut child) => {
            let (tx, rx) = mpsc::channel::<String>();
            if let Some(out) = child.stdout.take() {
                let tx2 = tx.clone();
                thread::spawn(move || pump_lines(out, tx2));
            }
            if let Some(err) = child.stderr.take() {
                let tx2 = tx.clone();
                thread::spawn(move || pump_lines(err, tx2));
            }
            rs.child = Some(child);
            rs.rx = Some(rx);
            drop(rs);
            app.status_msg = "Launched paper_processor.py. Streaming log…".into();
        }
        Err(e) => {
            rs.log.push(format!("ERROR spawning: {}", e));
            app.status_msg = format!("Spawn failed: {}", e);
        }
    }
}

fn pump_lines<R: std::io::Read + Send + 'static>(r: R, tx: Sender<String>) {
    let br = BufReader::new(r);
    for line in br.lines().map_while(Result::ok) {
        if tx.send(line).is_err() {
            break;
        }
    }
}

fn kill_running(app: &mut App) {
    let mut rs = app.run.lock().unwrap();
    if let Some(child) = rs.child.as_mut() {
        let _ = child.kill();
        rs.log.push("⟂ killed by user".into());
        app.status_msg = "Killed running process.".into();
    } else {
        app.status_msg = "No process running.".into();
    }
}

fn clear_log(app: &mut App) {
    let mut rs = app.run.lock().unwrap();
    rs.log.clear();
    rs.scroll = 0;
}

fn find_processor_script() -> PathBuf {
    // Prefer script alongside this binary's project directory.
    let candidates = [
        PathBuf::from(PROCESSOR_PY),
        PathBuf::from("../").join(PROCESSOR_PY),
        PathBuf::from("/home/jeb/programs/python_programs/paper_processor").join(PROCESSOR_PY),
    ];
    for c in candidates.iter() {
        if c.exists() {
            return c.clone();
        }
    }
    PathBuf::from(PROCESSOR_PY)
}

fn probe_health(h: &mut Health) {
    h.last_checked = Some(Instant::now());
    // Ollama
    h.ollama_ok = false;
    h.ollama_models = 0;
    if let Ok(resp) = ureq::get(&format!("{}/api/tags", OLLAMA_URL))
        .timeout(Duration::from_secs(3))
        .call()
    {
        if let Ok(v) = resp.into_json::<serde_json::Value>() {
            if let Some(arr) = v.get("models").and_then(|m| m.as_array()) {
                h.ollama_models = arr.len();
                h.ollama_ok = true;
            }
        }
    }
    // Graphviz dot
    h.graphviz_ok = Command::new("dot")
        .arg("-V")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    // GPU
    if let Ok(out) = Command::new("nvidia-smi")
        .args([
            "--query-gpu=name,memory.free,memory.total",
            "--format=csv,noheader,nounits",
        ])
        .output()
    {
        let s = String::from_utf8_lossy(&out.stdout);
        h.gpu_info = s
            .lines()
            .enumerate()
            .map(|(i, l)| {
                let parts: Vec<_> = l.split(',').map(|x| x.trim()).collect();
                if parts.len() >= 3 {
                    format!(
                        "GPU{}: {:<24}  free {:>6} MiB / {:>6} MiB",
                        i, parts[0], parts[1], parts[2]
                    )
                } else {
                    l.to_string()
                }
            })
            .collect::<Vec<_>>()
            .join("\n");
    } else {
        h.gpu_info = "nvidia-smi not available".into();
    }
    // Python + script + deps
    let script_found = find_processor_script().exists();
    let py = if Path::new(VENV_PYTHON).exists() {
        VENV_PYTHON
    } else {
        "python3"
    };
    let deps_ok = Command::new(py)
        .args(["-c", "import fitz, requests"])
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    h.python_ok = script_found && deps_ok;
}

// ──────────────────────────────────────────────────────────────────────
// Rendering
// ──────────────────────────────────────────────────────────────────────
fn draw(f: &mut Frame, app: &App) {
    let size = f.area();
    let vchunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3), // header tabs
            Constraint::Min(5),    // body
            Constraint::Length(3), // footer
        ])
        .split(size);

    draw_header(f, app, vchunks[0]);
    match app.tab {
        0 => draw_overview(f, app, vchunks[1]),
        1 => draw_scan(f, app, vchunks[1]),
        2 => draw_config(f, app, vchunks[1]),
        3 => draw_run(f, app, vchunks[1]),
        4 => draw_help(f, app, vchunks[1]),
        _ => {}
    }
    draw_footer(f, app, vchunks[2]);

    if app.show_help_overlay {
        draw_help_overlay(f, size);
    }
    if app.show_model_picker || app.show_code_picker {
        draw_model_picker(f, app, size);
    }
}

fn draw_header(f: &mut Frame, app: &App, area: Rect) {
    let titles: Vec<Line> = TAB_TITLES
        .iter()
        .map(|t| Line::from(Span::styled(*t, Style::default().fg(NEON_CYAN))))
        .collect();
    let tabs = Tabs::new(titles)
        .select(app.tab)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_type(BorderType::Rounded)
                .border_style(Style::default().fg(NEON_MAGENTA))
                .title(Span::styled(
                    " 🦞 paper-wizard ",
                    Style::default().fg(NEON_GREEN).add_modifier(Modifier::BOLD),
                )),
        )
        .highlight_style(
            Style::default()
                .fg(NEON_YELLOW)
                .bg(Color::Rgb(0x20, 0x00, 0x30))
                .add_modifier(Modifier::BOLD),
        )
        .divider(Span::styled("│", Style::default().fg(DIM_GREY)));
    f.render_widget(tabs, area);
}

fn draw_footer(f: &mut Frame, app: &App, area: Rect) {
    let left = Span::styled(
        format!(" ● {}", app.status_msg),
        Style::default().fg(NEON_GREEN),
    );
    let keys = " [Tab] panels   [q] quit   [?] help   [F5] re-probe ";
    let text = Line::from(vec![
        left,
        Span::raw("   "),
        Span::styled(keys, Style::default().fg(DIM_GREY)),
    ]);
    let p = Paragraph::new(text).block(
        Block::default()
            .borders(Borders::ALL)
            .border_type(BorderType::Rounded)
            .border_style(Style::default().fg(NEON_MAGENTA)),
    );
    f.render_widget(p, area);
}

// ── Overview tab ──────────────────────────────────────────────────────
fn draw_overview(f: &mut Frame, app: &App, area: Rect) {
    let cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(58), Constraint::Percentage(42)])
        .split(area);

    let body = vec![
        Line::from(vec![Span::styled(
            "OpenClaw / Ollama — AI·ML Paper Processing Pipeline",
            Style::default().fg(NEON_GREEN).add_modifier(Modifier::BOLD),
        )]),
        Line::from(""),
        Line::from("For every PDF in the target tree, the pipeline produces a"),
        Line::from("structured research dossier:"),
        Line::from(""),
        neon_row("  01_summary.md          ", NEON_CYAN, " comprehensive summary"),
        neon_row("  02_symbolic_logic.md   ", NEON_CYAN, " formal-logic / math"),
        neon_row("  03_cpp_examples.md     ", NEON_CYAN, " C++20/23 reference impl"),
        neon_row("  diagrams/ *.dot *.svg  ", NEON_CYAN, " 6 neon Graphviz diagrams"),
        neon_row("  04_extras.md           ", NEON_CYAN, " open questions, critique"),
        neon_row("  metadata.json          ", NEON_CYAN, " resume / audit trail"),
        Line::from(""),
        Line::from(Span::styled(
            "Model auto-selection by page count:",
            Style::default().fg(NEON_ORANGE),
        )),
        Line::from("  ≤  8 pp  →  deepseek-r1:8b          (~5 GB)"),
        Line::from("  ≤ 18 pp  →  deepseek-r1:14b         (~9 GB)"),
        Line::from("  > 18 pp  →  nemotron-3-nano-30b-small (~24 GB dual-GPU)"),
        Line::from("  C++ stage →  qwen3-coder:30b        (~17 GB dual-GPU)"),
        Line::from(""),
        Line::from(Span::styled(
            "Workflow:",
            Style::default().fg(NEON_MAGENTA),
        )),
        Line::from("  0. Run ./pp.py for interactive model selection before launch."),
        Line::from("  1. Press Tab → Scan, then S to index your corpus."),
        Line::from("  2. Tab → Config to set options (or pick Enter on a"),
        Line::from("     scan row to queue a single paper)."),
        Line::from("  3. Tab → Run, press L to launch. Log streams live."),
        Line::from("  4. Press X to abort; the next launch resumes via"),
        Line::from("     metadata.json — no wasted work."),
    ];

    let left = Paragraph::new(body)
        .wrap(Wrap { trim: false })
        .block(fancy_block(" Pipeline ", NEON_MAGENTA));
    f.render_widget(left, cols[0]);

    // Right column: health
    let h = &app.health;
    let icon = |ok: bool| {
        if ok {
            Span::styled("●", Style::default().fg(NEON_GREEN))
        } else {
            Span::styled("●", Style::default().fg(Color::Red))
        }
    };
    let rows = vec![
        Line::from(vec![
            icon(h.ollama_ok),
            Span::raw(format!(
                "  Ollama @ {}   ({} models)",
                OLLAMA_URL, h.ollama_models
            )),
        ]),
        Line::from(vec![
            icon(h.graphviz_ok),
            Span::raw("  Graphviz dot  (SVG rendering)"),
        ]),
        Line::from(vec![
            icon(h.python_ok),
            Span::raw(format!(
                "  Python + pymupdf + requests  ({})",
                if Path::new(VENV_PYTHON).exists() {
                    "venv"
                } else {
                    "python3"
                }
            )),
        ]),
        Line::from(""),
        Line::from(Span::styled(
            "GPU status:",
            Style::default().fg(NEON_ORANGE),
        )),
    ]
    .into_iter()
    .chain(
        h.gpu_info
            .lines()
            .map(|l| Line::from(Span::styled(l.to_string(), Style::default().fg(NEON_CYAN)))),
    )
    .chain(std::iter::once(Line::from("")))
    .chain(std::iter::once(Line::from(Span::styled(
        "Press F5 to re-probe.",
        Style::default().fg(DIM_GREY),
    ))))
    .collect::<Vec<_>>();

    let right = Paragraph::new(rows)
        .wrap(Wrap { trim: false })
        .block(fancy_block(" Environment ", NEON_GREEN));
    f.render_widget(right, cols[1]);
}

fn neon_row(k: &str, col: Color, v: &str) -> Line<'static> {
    Line::from(vec![
        Span::styled(k.to_string(), Style::default().fg(col)),
        Span::raw(v.to_string()),
    ])
}

// ── Scan tab ──────────────────────────────────────────────────────────
fn draw_scan(f: &mut Frame, app: &App, area: Rect) {
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(4), Constraint::Min(5)])
        .split(area);

    // Summary strip
    let (total, complete, partial, pending) = match &app.scan {
        Some(sr) => (sr.entries.len(), sr.complete, sr.partial, sr.pending),
        None => (0, 0, 0, 0),
    };
    let pct = if total > 0 {
        (complete as f64) / (total as f64)
    } else {
        0.0
    };
    let gauge = Gauge::default()
        .block(fancy_block(
            &format!(
                " Corpus: {}   ✅ {}   ⚠ {}   ⬜ {} ",
                total, complete, partial, pending
            ),
            NEON_MAGENTA,
        ))
        .gauge_style(Style::default().fg(NEON_GREEN).bg(Color::Rgb(0x10, 0x00, 0x20)))
        .ratio(pct)
        .label(format!(
            "{:.1}% complete   ({}/{} papers)",
            pct * 100.0,
            complete,
            total
        ));
    f.render_widget(gauge, rows[0]);

    let items: Vec<ListItem> = app
        .scan
        .as_ref()
        .map(|sr| {
            sr.entries
                .iter()
                .map(|e| {
                    let (icon, col) = match e.status {
                        Status::Complete => ("✅", NEON_GREEN),
                        Status::Partial => ("⚠", NEON_YELLOW),
                        Status::NotStarted => ("⬜", DIM_GREY),
                    };
                    let model = e.model.clone().unwrap_or_default();
                    ListItem::new(Line::from(vec![
                        Span::styled(format!(" {}  ", icon), Style::default().fg(col)),
                        Span::styled(
                            truncate(&e.rel_path, 74),
                            Style::default().fg(Color::White),
                        ),
                        Span::styled(
                            format!("  {}", model),
                            Style::default().fg(NEON_CYAN),
                        ),
                    ]))
                })
                .collect()
        })
        .unwrap_or_default();

    let title = if app.scanning {
        " Scanning… "
    } else if items.is_empty() {
        " Press [S] to scan — then [Enter] to queue a paper "
    } else {
        " Papers  (↑/↓ navigate · PgUp/PgDn jump · Enter = select → Config) "
    };

    let list = List::new(items)
        .block(fancy_block(title, NEON_CYAN))
        .highlight_style(
            Style::default()
                .bg(Color::Rgb(0x20, 0x00, 0x30))
                .fg(NEON_YELLOW)
                .add_modifier(Modifier::BOLD),
        )
        .highlight_symbol("▶ ");
    let mut state = app.scan_state.clone();
    f.render_stateful_widget(list, rows[1], &mut state);
}

fn truncate(s: &str, n: usize) -> String {
    if s.chars().count() <= n {
        s.to_string()
    } else {
        let cut: String = s.chars().take(n - 1).collect();
        format!("{}…", cut)
    }
}

// ── Config tab ────────────────────────────────────────────────────────
fn draw_config(f: &mut Frame, app: &App, area: Rect) {
    let fields = [
        ConfigField::PapersDir,
        ConfigField::Backend,
        ConfigField::Model,
        ConfigField::CodeModel,
        ConfigField::Paper,
        ConfigField::Reprocess,
        ConfigField::Workers,
    ];
    let labels = [
        "papers_dir",
        "--backend",
        "--model",
        "--code-model",
        "--paper",
        "--reprocess",
        "--workers",
    ];

    let values = [
        app.cfg.papers_dir.clone(),
        app.cfg.backend.as_str().to_string(),
        if app.cfg.model_override.is_empty() {
            "(auto-select)".to_string()
        } else {
            app.cfg.model_override.clone()
        },
        if app.cfg.code_model_override.is_empty() {
            "(qwen3-coder:30b)".to_string()
        } else {
            app.cfg.code_model_override.clone()
        },
        if app.cfg.single_paper.is_empty() {
            "(all papers)".to_string()
        } else {
            app.cfg.single_paper.clone()
        },
        if app.cfg.reprocess.is_empty() {
            "(off)".to_string()
        } else {
            app.cfg.reprocess.clone()
        },
        app.cfg.workers.to_string(),
    ];
    let hints = [
        "text: type to edit",
        "←/→: toggle ollama ↔ openclaw",
        "text: type model name  or  [i] interactive picker",
        "text: type model name  or  [i] interactive picker (C++ sections)",
        "text: basename or relative path",
        "←/→: cycle off|summary|logic|cpp|diagrams|extras|all",
        "←/→: 1 – 8  (⚠ OOM if >1 with 30B models)",
    ];

    let lines: Vec<Line> = fields
        .iter()
        .enumerate()
        .flat_map(|(i, field)| {
            let selected = *field == app.field;
            let label_style = if selected {
                Style::default()
                    .fg(NEON_YELLOW)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(NEON_CYAN)
            };
            let val_style = if selected {
                Style::default()
                    .fg(Color::White)
                    .bg(Color::Rgb(0x20, 0x00, 0x30))
            } else {
                Style::default().fg(Color::White)
            };
            let marker = if selected { "▶ " } else { "  " };
            vec![
                Line::from(vec![
                    Span::styled(marker, Style::default().fg(NEON_YELLOW)),
                    Span::styled(format!("{:<14}", labels[i]), label_style),
                    Span::raw("  "),
                    Span::styled(format!(" {} ", values[i]), val_style),
                ]),
                Line::from(vec![
                    Span::raw("                  "),
                    Span::styled(hints[i], Style::default().fg(DIM_GREY)),
                ]),
                Line::from(""),
            ]
        })
        .collect();

    let mut body = lines;
    body.push(Line::from(""));
    body.push(Line::from(Span::styled(
        "Press [Enter] to switch to Run tab · ↑/↓ select field · Tab switches panels",
        Style::default().fg(NEON_MAGENTA),
    )));

    let p = Paragraph::new(body)
        .wrap(Wrap { trim: false })
        .block(fancy_block(" Configuration ", NEON_MAGENTA));
    f.render_widget(p, area);
}

// ── Run tab ───────────────────────────────────────────────────────────
fn draw_run(f: &mut Frame, app: &App, area: Rect) {
    let rs = app.run.lock().unwrap();
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(5), Constraint::Min(5)])
        .split(area);

    // Status strip
    let elapsed = rs
        .started
        .map(|t| t.elapsed().as_secs())
        .unwrap_or(0);
    let (h, m, s) = (elapsed / 3600, (elapsed / 60) % 60, elapsed % 60);
    let state_col = if rs.finished {
        match rs.exit_code {
            Some(0) => NEON_GREEN,
            _ => Color::Red,
        }
    } else if rs.child.is_some() {
        NEON_YELLOW
    } else {
        DIM_GREY
    };
    let state_label = if rs.finished {
        match rs.exit_code {
            Some(0) => "FINISHED ✓".into(),
            Some(c) => format!("EXITED ({})", c),
            None => "EXITED".into(),
        }
    } else if rs.child.is_some() {
        "RUNNING".into()
    } else {
        "IDLE".into()
    };

    let status_lines = vec![
        Line::from(vec![
            Span::styled(
                format!(" {} ", state_label),
                Style::default()
                    .fg(Color::Black)
                    .bg(state_col)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw(format!("  ⏱  {:02}:{:02}:{:02}", h, m, s)),
            Span::raw(format!("   📄 done: {}", rs.papers_done)),
        ]),
        Line::from(vec![
            Span::styled("  paper:   ", Style::default().fg(NEON_CYAN)),
            Span::styled(
                rs.current_paper.clone(),
                Style::default().fg(Color::White).add_modifier(Modifier::BOLD),
            ),
        ]),
        Line::from(vec![
            Span::styled("  section: ", Style::default().fg(NEON_CYAN)),
            Span::styled(
                rs.current_section.clone(),
                Style::default().fg(NEON_MAGENTA),
            ),
        ]),
    ];
    let status_p = Paragraph::new(status_lines).block(fancy_block(
        " Status — [L] launch · [X] kill · [c] clear · [↑/↓/PgUp/PgDn] scroll · [End] autoscroll ",
        NEON_MAGENTA,
    ));
    f.render_widget(status_p, rows[0]);

    // Log pane
    let log_area = rows[1];
    let inner_h = log_area.height.saturating_sub(2);
    let total = rs.log.len() as u16;
    let scroll = if rs.autoscroll {
        total.saturating_sub(inner_h)
    } else {
        rs.scroll.min(total.saturating_sub(1))
    };

    let visible: Vec<Line> = rs
        .log
        .iter()
        .skip(scroll as usize)
        .take(inner_h as usize)
        .map(|l| colourise_log_line(l))
        .collect();

    let log_p = Paragraph::new(visible).block(fancy_block(
        &format!(
            " Log  ({} lines  scroll={}/{}{}) ",
            total,
            scroll,
            total.saturating_sub(inner_h),
            if rs.autoscroll { "  AUTO" } else { "" }
        ),
        NEON_CYAN,
    ));
    f.render_widget(log_p, rows[1]);
}

fn colourise_log_line(l: &str) -> Line<'static> {
    let (col, modif) = if l.contains("❌") || l.contains("Error") || l.contains("error") {
        (Color::Red, Modifier::BOLD)
    } else if l.contains("✅") || l.contains("✓") || l.contains("🟢") {
        (NEON_GREEN, Modifier::empty())
    } else if l.contains("⚠") || l.contains("WARNING") {
        (NEON_YELLOW, Modifier::empty())
    } else if l.starts_with('$') {
        (NEON_MAGENTA, Modifier::BOLD)
    } else if l.contains("📄") || l.contains("📝") || l.contains("🔣") || l.contains("💻")
        || l.contains("📊") || l.contains("💡")
    {
        (NEON_CYAN, Modifier::BOLD)
    } else {
        (Color::White, Modifier::empty())
    };
    Line::from(Span::styled(
        l.to_string(),
        Style::default().fg(col).add_modifier(modif),
    ))
}

// ── Help tab ──────────────────────────────────────────────────────────
fn draw_help(f: &mut Frame, _app: &App, area: Rect) {
    let text = vec![
        Line::from(Span::styled(
            "Keybindings",
            Style::default().fg(NEON_GREEN).add_modifier(Modifier::BOLD),
        )),
        Line::from(""),
        Line::from(" Global"),
        kv("   Tab / Shift-Tab", "switch panels"),
        kv("   q   or  Ctrl-C ", "quit"),
        kv("   ?              ", "pop-up help overlay"),
        kv("   F5             ", "re-probe environment (Overview)"),
        Line::from(""),
        Line::from(" Scan panel"),
        kv("   s              ", "start recursive scan"),
        kv("   ↑/↓ · j/k      ", "navigate list"),
        kv("   PgUp/PgDn      ", "jump 20 rows"),
        kv("   Enter          ", "queue highlighted paper → Config"),
        Line::from(""),
        Line::from(" Config panel"),
        kv("   ↑/↓ · j/k      ", "next/prev field"),
        kv("   ←/→            ", "cycle value (backend · reprocess · workers)"),
        kv("   type / Backspace", "edit text fields"),
        kv("   i              ", "interactive model picker (--model / --code-model fields)"),
        kv("   Enter          ", "jump to Run"),
        Line::from(""),
        Line::from(" Run panel"),
        kv("   L              ", "launch paper_processor.py"),
        kv("   X              ", "kill running process"),
        kv("   c              ", "clear log"),
        kv("   ↑/↓/PgUp/PgDn  ", "scroll log"),
        kv("   End            ", "re-enable autoscroll"),
        Line::from(""),
        Line::from(Span::styled(
            "Tips",
            Style::default().fg(NEON_ORANGE).add_modifier(Modifier::BOLD),
        )),
        Line::from("  • The pipeline is resumable — metadata.json tracks"),
        Line::from("    which sections completed. Kill anytime."),
        Line::from("  • On worlock, OLLAMA_MAX_LOADED_MODELS=1 forces a"),
        Line::from("    model reload between summary-model and code-model"),
        Line::from("    (~60s). Forcing a single --model avoids this."),
        Line::from("  • --workers > 1 will OOM with 30B models (22 GB"),
        Line::from("    total VRAM); safe only with small models."),
        Line::from("  • Scan uses the same slug logic as the Python — if"),
        Line::from("    slugs drift, the scan status may miss completions."),
    ];
    let p = Paragraph::new(text)
        .wrap(Wrap { trim: false })
        .block(fancy_block(" Help ", NEON_CYAN));
    f.render_widget(p, area);
}

fn kv(k: &str, v: &str) -> Line<'static> {
    Line::from(vec![
        Span::styled(k.to_string(), Style::default().fg(NEON_YELLOW)),
        Span::raw("  "),
        Span::styled(v.to_string(), Style::default().fg(Color::White)),
    ])
}

fn draw_model_picker(f: &mut Frame, app: &App, area: Rect) {
    let list_data: &[ModelEntry] = if app.show_model_picker {
        KNOWN_GOOD_MODELS
    } else {
        KNOWN_GOOD_CODE_MODELS
    };
    let title = if app.show_model_picker {
        " Main Model — ↑/↓ · Enter select · Esc cancel "
    } else {
        " C++ / Code Model — ↑/↓ · Enter select · Esc cancel "
    };

    let h = (list_data.len() as u16 + 2).min(area.height.saturating_sub(4));
    let w = 100.min(area.width.saturating_sub(4));
    let x = area.x + (area.width.saturating_sub(w)) / 2;
    let y = area.y + (area.height.saturating_sub(h)) / 2;
    let r = Rect::new(x, y, w, h);
    f.render_widget(Clear, r);

    let items: Vec<ListItem> = list_data
        .iter()
        .enumerate()
        .map(|(i, m)| {
            ListItem::new(Line::from(vec![
                Span::raw(format!("  {:2}  ", i + 1)),
                Span::styled(format!("{:<36}", m.name), Style::default().fg(NEON_CYAN)),
                Span::raw("  "),
                Span::styled(format!("{:<8}", m.vram), Style::default().fg(NEON_GREEN)),
                Span::raw("  "),
                Span::styled(m.role, Style::default().fg(DIM_GREY)),
            ]))
        })
        .collect();

    let list = List::new(items)
        .block(fancy_block(title, NEON_MAGENTA))
        .highlight_style(
            Style::default()
                .fg(NEON_YELLOW)
                .add_modifier(Modifier::BOLD),
        )
        .highlight_symbol("▶ ");

    let mut state = ListState::default();
    state.select(Some(app.picker_idx));
    f.render_stateful_widget(list, r, &mut state);
}

fn draw_help_overlay(f: &mut Frame, area: Rect) {
    let w = 60.min(area.width.saturating_sub(4));
    let h = 14.min(area.height.saturating_sub(4));
    let x = area.x + (area.width.saturating_sub(w)) / 2;
    let y = area.y + (area.height.saturating_sub(h)) / 2;
    let r = Rect::new(x, y, w, h);
    f.render_widget(Clear, r);
    let lines = vec![
        Line::from(Span::styled(
            "paper-wizard — quick keys",
            Style::default().fg(NEON_GREEN).add_modifier(Modifier::BOLD),
        )),
        Line::from(""),
        kv("  Tab / Shift-Tab", "switch panels"),
        kv("  q              ", "quit"),
        kv("  F5             ", "re-probe environment"),
        kv("  s              ", "scan (Scan panel)"),
        kv("  i              ", "interactive model picker (Config → --model / --code-model)"),
        kv("  L              ", "launch (Run panel)"),
        kv("  X              ", "kill (Run panel)"),
        Line::from(""),
        Line::from(Span::styled(
            "Press any key to dismiss.",
            Style::default().fg(DIM_GREY),
        )),
    ];
    let p = Paragraph::new(lines)
        .alignment(Alignment::Left)
        .block(fancy_block(" Help ", NEON_MAGENTA));
    f.render_widget(p, r);
}

fn fancy_block(title: &str, col: Color) -> Block<'static> {
    Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(col))
        .title(Span::styled(
            title.to_string(),
            Style::default().fg(col).add_modifier(Modifier::BOLD),
        ))
}

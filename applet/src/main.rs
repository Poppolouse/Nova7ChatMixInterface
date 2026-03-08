use cosmic::app::{Core, Task};
use cosmic::iced::platform_specific::shell::wayland::commands::popup::{destroy_popup, get_popup};
use cosmic::iced::window::Id;
use cosmic::iced::{Limits, Subscription, theme};
use cosmic::widget::{self, button, list_column, settings, text};
use cosmic::Element;
use log::warn;
use serde::Deserialize;
use std::env;
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::time::{Duration, SystemTime};

// ═══════════════════════════════════════════════════════════════════════════
// Constants
// ═══════════════════════════════════════════════════════════════════════════

const APP_ID: &str = "io.github.poppolouse.CosmicAppletNovaChatMix";
const SERVICE_NAME: &str = "nova7-mixer.service";
const EXPECTED_DEVICE_NAME: &str = "Arctis Nova 7";
const ICON_CONNECTED: &str = "audio-headphones-symbolic";
const ICON_DISCONNECTED: &str = "audio-card-symbolic";

const GAME_SINK: &str = "GameMix";
const CHAT_SINK: &str = "ChatMix";

const STATE_FILE_MAX_AGE: Duration = Duration::from_secs(10);
const BATTERY_REFRESH_INTERVAL: Duration = Duration::from_secs(5);

/// Apps known to manage their own audio routing, which may conflict with sink moves.
const RESTRICTED_APPS: &[&str] = &["discord", "teams", "zoom", "slack"];

// ═══════════════════════════════════════════════════════════════════════════
// Data Model
// ═══════════════════════════════════════════════════════════════════════════

#[derive(Debug, Clone, Default, Deserialize)]
#[allow(dead_code)]
struct StatusFile {
    chatmix_level: Option<i32>,
    #[allow(dead_code)]
    game_volume: Option<i32>,
    #[allow(dead_code)]
    chat_volume: Option<i32>,
    battery_level: Option<i32>,
    battery_charging: Option<bool>,
    headset_connected: Option<bool>,
    timestamp: Option<String>,
}

#[derive(Debug, Clone)]
struct SinkInput {
    id: u32,
    app_name: String,
    sink_name: String,
    is_restricted: bool,
}

#[derive(Debug, Clone, Default)]
struct AppStatus {
    headset_connected: bool,
    battery_level: Option<i32>,
    battery_charging: bool,
    chatmix_raw: Option<i32>,
    game_volume: i32,
    chat_volume: i32,
    service_active: String,
    service_enabled: String,
    controller_status: String,
    sink_inputs: Vec<SinkInput>,
    last_log_line: String,
    last_error: Option<String>,
}

// ═══════════════════════════════════════════════════════════════════════════
// Data Gathering
// ═══════════════════════════════════════════════════════════════════════════

fn state_file_path() -> PathBuf {
    let home = env::var("HOME").unwrap_or_else(|_| "/tmp".into());
    PathBuf::from(home).join(".local/state/nova7-chatmix/status.json")
}

fn read_state_file() -> Option<StatusFile> {
    let path = state_file_path();
    let age = fs::metadata(&path)
        .ok()?
        .modified()
        .ok()
        .and_then(|m| SystemTime::now().duration_since(m).ok())?;
    if age > STATE_FILE_MAX_AGE {
        return None;
    }
    serde_json::from_str(&fs::read_to_string(path).ok()?).ok()
}

/// Convert ChatMix wheel position (0-128) to (game%, chat%) volumes.
/// Matches the Python mixer algorithm in `mixer/nova7_mixer.py`.
fn mix_to_volumes(mix: i32) -> (i32, i32) {
    let mix = mix.clamp(0, 128);
    match mix.cmp(&64) {
        std::cmp::Ordering::Greater => ((200 - mix * 100 / 64).max(0), 100),
        std::cmp::Ordering::Less => (100, (mix * 100 / 64).max(0)),
        std::cmp::Ordering::Equal => (100, 100),
    }
}

fn gather_battery() -> (Option<i32>, bool) {
    if let Some(sf) = read_state_file() {
        if sf.headset_connected == Some(false) {
            return (None, false);
        }
        if let Some(level) = sf.battery_level {
            if level >= 0 {
                return (Some(level.clamp(0, 100)), sf.battery_charging.unwrap_or(false));
            }
            return (None, false);
        }
    }
    match command_text("headsetcontrol", &["-b", "-o", "short"]) {
        Some(s) if s.trim().parse::<i32>().ok().is_some_and(|v| v < 0) => (None, false),
        Some(s) if s.trim() == "-1" => (None, true),
        Some(s) => (s.trim().parse::<i32>().ok().map(|v| v.clamp(0, 100)), false),
        None => (None, false),
    }
}

fn gather_chatmix() -> Option<i32> {
    if let Some(sf) = read_state_file() {
        if let Some(level) = sf.chatmix_level {
            return Some(level);
        }
    }
    command_text("headsetcontrol", &["-m", "-o", "short"])
        .and_then(|s| s.trim().parse().ok())
}

fn headsetcontrol_ready() -> bool {
    command_text("headsetcontrol", &["-m", "-o", "short"])
        .and_then(|s| s.trim().parse::<i32>().ok())
        .is_some()
}

fn detect_headset() -> bool {
    if let Some(sf) = read_state_file() {
        if let Some(c) = sf.headset_connected {
            return c;
        }
    }
    // USB presence is not enough: the Nova 7 dongle can stay attached while the
    // headset itself is powered off. Treat the headset as connected only when
    // headsetcontrol can read live data from it.
    headsetcontrol_ready()
}

/// Map PulseAudio sink indices to sink names via `pactl list sinks short`.
fn get_sink_name_map() -> Vec<(String, String)> {
    command_stdout("pactl", &["list", "sinks", "short"])
        .map(|out| {
            out.lines()
                .filter_map(|l| {
                    let mut parts = l.split('\t');
                    Some((parts.next()?.to_string(), parts.next()?.to_string()))
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Parse `pactl list sink-inputs` to extract running audio applications,
/// their current sink assignment, and whether they are known restricted apps.
fn parse_sink_inputs() -> Vec<SinkInput> {
    let sink_map = get_sink_name_map();
    let output = match command_stdout("pactl", &["list", "sink-inputs"]) {
        Some(o) => o,
        None => return Vec::new(),
    };

    let mut results = Vec::new();
    let mut id: Option<u32> = None;
    let mut sink_idx: Option<String> = None;
    let mut app_name: Option<String> = None;

    let flush = |results: &mut Vec<SinkInput>,
                 id: Option<u32>,
                 app_name: Option<String>,
                 sink_idx: Option<String>,
                 sink_map: &[(String, String)]| {
        if let (Some(id), Some(app)) = (id, app_name) {
            let idx = sink_idx.unwrap_or_default();
            let sink_name = sink_map
                .iter()
                .find(|(i, _)| *i == idx)
                .map(|(_, n)| n.clone())
                .unwrap_or_else(|| format!("sink#{idx}"));
            let is_restricted = RESTRICTED_APPS
                .iter()
                .any(|r| app.to_ascii_lowercase().contains(r));
            results.push(SinkInput {
                id,
                app_name: app,
                sink_name,
                is_restricted,
            });
        }
    };

    for line in output.lines() {
        let t = line.trim();
        if let Some(rest) = t.strip_prefix("Sink Input #") {
            flush(&mut results, id, app_name.take(), sink_idx.take(), &sink_map);
            id = rest.parse().ok();
        } else if let Some(rest) = t.strip_prefix("Sink:") {
            sink_idx = Some(rest.trim().to_string());
        } else if t.starts_with("application.name") {
            app_name = t
                .split_once('=')
                .map(|(_, v)| v.trim().trim_matches('"').to_string());
        }
    }
    flush(&mut results, id, app_name, sink_idx, &sink_map);

    results
}

impl AppStatus {
    /// Refresh fast-changing data: connection, chatmix, service, audio routing.
    fn gather_live(&mut self) {
        self.headset_connected = detect_headset();

        if self.headset_connected {
            self.chatmix_raw = gather_chatmix();
            if let Some(mix) = self.chatmix_raw {
                let (g, c) = mix_to_volumes(mix);
                self.game_volume = g;
                self.chat_volume = c;
            }
        } else {
            self.chatmix_raw = None;
            self.game_volume = 0;
            self.chat_volume = 0;
        }

        self.service_active =
            command_text("systemctl", &["--user", "is-active", SERVICE_NAME])
                .unwrap_or_else(|| "unknown".into());
        self.service_enabled =
            command_text("systemctl", &["--user", "is-enabled", SERVICE_NAME])
                .unwrap_or_else(|| "unknown".into());

        self.sink_inputs = parse_sink_inputs();

        self.controller_status = match command_text("headsetcontrol", &["-m", "-o", "short"]) {
            Some(o) if o.trim().parse::<i32>().is_ok() => "Ready".into(),
            Some(o) if o.contains("Could not open device") => "No permissions (udev)".into(),
            Some(_) => "Error".into(),
            None => "Unavailable".into(),
        };

        self.last_log_line = command_stdout(
            "journalctl",
            &["--user", "-u", SERVICE_NAME, "-n", "1", "--no-pager"],
        )
        .and_then(|o| o.lines().last().map(ToOwned::to_owned))
        .unwrap_or_else(|| "No logs yet".into());
    }

    /// Refresh slow-changing data: battery level / charging state.
    fn gather_battery(&mut self) {
        let (level, charging) = gather_battery();
        self.battery_level = level;
        self.battery_charging = charging;
    }

    fn gather_all() -> Self {
        let mut s = Self::default();
        s.gather_live();
        s.gather_battery();
        s
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Messages & Application
// ═══════════════════════════════════════════════════════════════════════════

#[derive(Debug, Clone)]
enum Message {
    TogglePopup,
    PopupClosed(Id),
    RefreshLive,
    RefreshBattery,
    StartService,
    StopService,
    RestartService,
    RecreateMixSinks,
    MoveSinkInput(u32, String),
    ToggleServiceSection,
}

struct Applet {
    core: Core,
    popup: Option<Id>,
    status: AppStatus,
    show_service_section: bool,
}

impl cosmic::Application for Applet {
    type Executor = cosmic::executor::Default;
    type Flags = ();
    type Message = Message;

    const APP_ID: &'static str = APP_ID;

    fn core(&self) -> &Core {
        &self.core
    }

    fn core_mut(&mut self) -> &mut Core {
        &mut self.core
    }

    fn init(core: Core, _flags: ()) -> (Self, Task<Self::Message>) {
        (
            Self {
                core,
                popup: None,
                status: AppStatus::gather_all(),
                show_service_section: false,
            },
            Task::none(),
        )
    }

    fn on_close_requested(&self, id: Id) -> Option<Message> {
        Some(Message::PopupClosed(id))
    }

    fn subscription(&self) -> Subscription<Message> {
        Subscription::batch(vec![
            cosmic::iced::time::every(Duration::from_secs(3)).map(|_| Message::RefreshLive),
            cosmic::iced::time::every(BATTERY_REFRESH_INTERVAL).map(|_| Message::RefreshBattery),
        ])
    }

    fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::TogglePopup => {
                if let Some(id) = self.popup.take() {
                    return destroy_popup(id);
                }

                self.status = AppStatus::gather_all();
                let new_id = Id::unique();
                self.popup = Some(new_id);

                if let Some(main_id) = self.core.main_window_id() {
                    let mut ps =
                        self.core
                            .applet
                            .get_popup_settings(main_id, new_id, None, None, None);
                    ps.positioner.size_limits = Limits::NONE;
                    return get_popup(ps);
                }
            }
            Message::PopupClosed(id) => {
                if self.popup.as_ref() == Some(&id) {
                    self.popup = None;
                }
            }
            Message::RefreshLive => {
                let err = self.status.last_error.take();
                let (bat, chg) = (self.status.battery_level, self.status.battery_charging);
                let was_connected = self.status.headset_connected;
                self.status.gather_live();
                self.status.last_error = err;
                self.status.battery_level = bat;
                self.status.battery_charging = chg;
                if self.status.headset_connected && (!was_connected || self.status.battery_level.is_none()) {
                    self.status.gather_battery();
                }
            }
            Message::RefreshBattery => {
                self.status.gather_battery();
            }
            Message::StartService => self.run_service_action("start"),
            Message::StopService => self.run_service_action("stop"),
            Message::RestartService => self.run_service_action("restart"),
            Message::RecreateMixSinks => {
                self.status.last_error = recreate_mix_sinks().err();
                self.status.gather_live();
            }
            Message::MoveSinkInput(input_id, target) => {
                match Command::new("pactl")
                    .args(["move-sink-input", &input_id.to_string(), &target])
                    .output()
                {
                    Ok(o) if o.status.success() => self.status.last_error = None,
                    Ok(o) => {
                        let e = String::from_utf8_lossy(&o.stderr).trim().to_string();
                        self.status.last_error = Some(if e.is_empty() {
                            "Move sink input failed".into()
                        } else {
                            e
                        });
                    }
                    Err(e) => self.status.last_error = Some(e.to_string()),
                }
                self.status.sink_inputs = parse_sink_inputs();
            }
            Message::ToggleServiceSection => {
                self.show_service_section = !self.show_service_section;
            }
        }

        Task::none()
    }

    fn view(&self) -> Element<'_, Message> {
        let icon = if self.status.headset_connected {
            ICON_CONNECTED
        } else {
            ICON_DISCONNECTED
        };
        self.core
            .applet
            .icon_button(icon)
            .on_press(Message::TogglePopup)
            .into()
    }

    fn view_window(&self, _id: Id) -> Element<'_, Message> {
        self.core
            .applet
            .popup_container(widget::scrollable(self.popup_content()))
            .limits(
                Limits::NONE
                    .min_width(380.0)
                    .max_width(440.0)
                    .min_height(200.0)
                    .max_height(700.0),
            )
            .into()
    }

    fn style(&self) -> Option<theme::Style> {
        Some(cosmic::applet::style())
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// UI
// ═══════════════════════════════════════════════════════════════════════════

impl Applet {
    fn popup_content(&self) -> widget::ListColumn<'_, Message> {
        let mut c = list_column().padding(8).spacing(4);

        // ── Header: device name + connection status ──
        let status_label = if self.status.headset_connected {
            "● Connected"
        } else {
            "○ Disconnected"
        };
        c = c
            .add(settings::item(EXPECTED_DEVICE_NAME, text::body(status_label)))
            .add(widget::divider::horizontal::default());

        // ── Battery (only when headset is connected) ──
        if self.status.headset_connected {
            let bat_text = if self.status.battery_charging {
                "⚡ Charging".to_string()
            } else if let Some(lvl) = self.status.battery_level {
                format!("{lvl}%")
            } else {
                "Unknown".to_string()
            };
            c = c.add(settings::item("Battery", text::body(bat_text)));
            if let Some(lvl) = self.status.battery_level {
                c = c.add(widget::progress_bar(0.0..=100.0, lvl as f32));
            }
            c = c.add(widget::divider::horizontal::default());
        }

        // ── ChatMix balance (read-only visualization) ──
        if self.status.headset_connected {
            c = c.add(widget::text::heading("ChatMix Balance"));
            if let Some(raw) = self.status.chatmix_raw {
                let game_label = format!("Game {}%", self.status.game_volume);
                let chat_label = format!("Chat {}%", self.status.chat_volume);
                c = c
                    .add(settings::item(
                        game_label,
                        widget::progress_bar(0.0..=100.0, self.status.game_volume as f32),
                    ))
                    .add(settings::item(
                        chat_label,
                        widget::progress_bar(0.0..=100.0, self.status.chat_volume as f32),
                    ))
                    .add(text::caption(format!("Wheel position: {raw}/128")));
            } else {
                c = c.add(text::body("No ChatMix data"));
            }
            c = c.add(widget::divider::horizontal::default());
        }

        // ── Per-app audio routing ──
        c = c.add(widget::text::heading("Audio Routing"));
        if self.status.sink_inputs.is_empty() {
            c = c.add(text::body("No audio applications playing"));
        } else {
            for inp in &self.status.sink_inputs {
                let on_game = inp.sink_name.contains(GAME_SINK);
                let on_chat = inp.sink_name.contains(CHAT_SINK);
                let tag = if on_game {
                    "Game"
                } else if on_chat {
                    "Chat"
                } else {
                    "Other"
                };

                let mut game_btn = button::standard("Game");
                if !on_game {
                    game_btn = game_btn
                        .on_press(Message::MoveSinkInput(inp.id, GAME_SINK.to_string()));
                }
                let mut chat_btn = button::standard("Chat");
                if !on_chat {
                    chat_btn = chat_btn
                        .on_press(Message::MoveSinkInput(inp.id, CHAT_SINK.to_string()));
                }
                let btns = widget::row().spacing(4).push(game_btn).push(chat_btn);

                let app_label = format!("{} [{}]", inp.app_name, tag);
                c = c.add(settings::item(app_label, btns));

                if inp.is_restricted {
                    let warn_text =
                        format!("⚠ {} may override audio routing", inp.app_name);
                    c = c.add(text::caption(warn_text));
                }
            }
        }
        c = c.add(widget::divider::horizontal::default());

        // ── Service controls (collapsible) ──
        let arrow = if self.show_service_section {
            "▼"
        } else {
            "▶"
        };
        let toggle_text = format!("{arrow} Service Controls");
        c = c.add(button::text(toggle_text).on_press(Message::ToggleServiceSection));

        if self.show_service_section {
            let actions = widget::row()
                .spacing(8)
                .push(button::standard("Start").on_press(Message::StartService))
                .push(button::destructive("Stop").on_press(Message::StopService))
                .push(button::standard("Restart").on_press(Message::RestartService));

            c = c
                .add(settings::item(
                    "Service",
                    text::body(self.status.service_active.clone()),
                ))
                .add(settings::item(
                    "Autostart",
                    text::body(self.status.service_enabled.clone()),
                ))
                .add(settings::item(
                    "Controller",
                    text::body(self.status.controller_status.clone()),
                ))
                .add(settings::item("Actions", actions))
                .add(settings::item(
                    "Tools",
                    button::text("Recreate Sinks").on_press(Message::RecreateMixSinks),
                ))
                .add(settings::item(
                    "Last log",
                    text::caption(self.status.last_log_line.clone()),
                ));
        } else {
            let dot = if self.status.service_active == "active" {
                "●"
            } else {
                "○"
            };
            let summary = format!(
                "{dot} Service: {} | Autostart: {}",
                self.status.service_active, self.status.service_enabled
            );
            c = c.add(text::caption(summary));
        }

        // ── Error display ──
        if let Some(err) = &self.status.last_error {
            c = c
                .add(widget::divider::horizontal::default())
                .add(settings::item("Error", text::body(err.clone())));
        }

        c
    }

    fn run_service_action(&mut self, action: &str) {
        let result = Command::new("systemctl")
            .args(["--user", action, SERVICE_NAME])
            .output();

        self.status.gather_live();
        self.status.last_error = match result {
            Ok(o) if o.status.success() => None,
            Ok(o) => {
                let e = String::from_utf8_lossy(&o.stderr).trim().to_string();
                Some(if e.is_empty() {
                    format!("systemctl {action} failed")
                } else {
                    e
                })
            }
            Err(e) => Some(e.to_string()),
        };
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Shell Command Helpers
// ═══════════════════════════════════════════════════════════════════════════

fn command_stdout(program: &str, args: &[&str]) -> Option<String> {
    let output = Command::new(program).args(args).output().ok()?;
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if !output.status.success() {
        warn!("{program} {args:?} exited with {:?}", output.status.code());
    }
    (!stdout.is_empty()).then_some(stdout)
}

fn command_text(program: &str, args: &[&str]) -> Option<String> {
    let output = Command::new(program).args(args).output().ok()?;
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    if !output.status.success() {
        warn!("{program} {args:?} exited with {:?}", output.status.code());
    }
    if !stdout.is_empty() {
        Some(stdout)
    } else if !stderr.is_empty() {
        Some(stderr)
    } else {
        None
    }
}

fn recreate_mix_sinks() -> Result<(), String> {
    let home = env::var("HOME").map_err(|e| e.to_string())?;
    Command::new(format!("{home}/.local/bin/nova7-virtualaudio"))
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

// ═══════════════════════════════════════════════════════════════════════════
// Entry Point
// ═══════════════════════════════════════════════════════════════════════════

fn main() -> cosmic::iced::Result {
    env_logger::init_from_env(
        env_logger::Env::default()
            .filter_or("RUST_LOG", "warn")
            .write_style_or("RUST_LOG_STYLE", "always"),
    );

    cosmic::applet::run::<Applet>(())
}

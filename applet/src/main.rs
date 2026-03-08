use cosmic::app::{Core, Task};
use cosmic::iced::platform_specific::shell::wayland::commands::popup::{destroy_popup, get_popup};
use cosmic::iced::window::Id;
use cosmic::iced::{Limits, Subscription, theme};
use cosmic::widget::{self, button, list_column, settings, text};
use cosmic::Element;
use log::warn;
use std::env;
use std::process::Command;
use std::time::Duration;

const APP_ID: &str = "io.github.poppolouse.CosmicAppletNovaChatMix";
const SERVICE_NAME: &str = "nova7-mixer.service";
const EXPECTED_USB_ID: &str = "1038:2202";
const EXPECTED_DEVICE_NAME: &str = "Arctis Nova 7";
const PANEL_ICON: &str = "audio-headphones-symbolic";

#[derive(Debug, Clone, Default)]
struct AppStatus {
    service_active: String,
    service_enabled: String,
    compatible_device_label: String,
    controller_status: String,
    devices: Vec<String>,
    last_log_line: String,
    last_error: Option<String>,
}

impl AppStatus {
    fn gather() -> Self {
        let service_active = command_text("systemctl", ["--user", "is-active", SERVICE_NAME])
            .unwrap_or_else(|| "unknown".to_string());
        let service_enabled = command_text("systemctl", ["--user", "is-enabled", SERVICE_NAME])
            .unwrap_or_else(|| "unknown".to_string());

        let devices = command_stdout("lsusb", [])
            .map(|output| {
                output
                    .lines()
                    .filter(|line| line.contains("ID 1038:") || line.contains("SteelSeries"))
                    .map(ToOwned::to_owned)
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();

        let compatible_device_found = devices.iter().any(|line| {
            line.contains(EXPECTED_USB_ID)
                || line
                    .to_ascii_lowercase()
                    .contains(&EXPECTED_DEVICE_NAME.to_ascii_lowercase())
        });

        let compatible_device_label = if compatible_device_found {
            format!("Detected ({EXPECTED_USB_ID})")
        } else {
            format!("Not detected ({EXPECTED_USB_ID})")
        };

        let controller_status = match command_text("headsetcontrol", ["-m", "-o", "short"]) {
            Some(output) if output.trim().parse::<i32>().is_ok() => "Ready".to_string(),
            Some(output) if output.contains("Could not open device") => {
                "Blocked by device permissions (udev rule needed)".to_string()
            }
            Some(output) => output,
            None => "Unavailable".to_string(),
        };

        let last_log_line = command_stdout(
            "journalctl",
            ["--user", "-u", SERVICE_NAME, "-n", "1", "--no-pager"],
        )
        .and_then(|output| output.lines().last().map(ToOwned::to_owned))
        .unwrap_or_else(|| "No service logs yet.".to_string());

        Self {
            service_active,
            service_enabled,
            compatible_device_label,
            controller_status,
            devices,
            last_log_line,
            last_error: None,
        }
    }
}

#[derive(Debug, Clone)]
enum Message {
    TogglePopup,
    PopupClosed(Id),
    Refresh,
    StartService,
    StopService,
    RestartService,
    RecreateMixSinks,
}

struct Applet {
    core: Core,
    popup: Option<Id>,
    status: AppStatus,
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

    fn init(core: Core, _flags: Self::Flags) -> (Self, Task<Self::Message>) {
        (
            Self {
                core,
                popup: None,
                status: AppStatus::gather(),
            },
            Task::none(),
        )
    }

    fn on_close_requested(&self, id: Id) -> Option<Message> {
        Some(Message::PopupClosed(id))
    }

    fn subscription(&self) -> Subscription<Message> {
        cosmic::iced::time::every(Duration::from_secs(5)).map(|_| Message::Refresh)
    }

    fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::TogglePopup => {
                if let Some(id) = self.popup.take() {
                    return destroy_popup(id);
                }

                self.status = AppStatus::gather();
                let new_id = Id::unique();
                self.popup = Some(new_id);

                if let Some(main_id) = self.core.main_window_id() {
                    let mut popup_settings = self
                        .core
                        .applet
                        .get_popup_settings(main_id, new_id, None, None, None);
                    popup_settings.positioner.size_limits = Limits::NONE;
                    return get_popup(popup_settings);
                }
            }
            Message::PopupClosed(id) => {
                if self.popup.as_ref() == Some(&id) {
                    self.popup = None;
                }
            }
            Message::Refresh => {
                let previous_error = self.status.last_error.take();
                self.status = AppStatus::gather();
                self.status.last_error = previous_error;
            }
            Message::StartService => self.run_service_action("start"),
            Message::StopService => self.run_service_action("stop"),
            Message::RestartService => self.run_service_action("restart"),
            Message::RecreateMixSinks => {
                self.status.last_error = recreate_mix_sinks().err();
                let previous_error = self.status.last_error.take();
                self.status = AppStatus::gather();
                self.status.last_error = previous_error;
            }
        }

        Task::none()
    }

    fn view(&self) -> Element<'_, Message> {
        self.core
            .applet
            .icon_button(PANEL_ICON)
            .on_press(Message::TogglePopup)
            .into()
    }

    fn view_window(&self, _id: Id) -> Element<'_, Message> {
        let detected_devices = if self.status.devices.is_empty() {
            "No SteelSeries USB devices found".to_string()
        } else {
            self.status.devices.join("\n")
        };

        let mut content = list_column()
            .padding(8)
            .spacing(8)
            .add(widget::text::heading("Nova 7 ChatMix"))
            .add(settings::item(
                "Service",
                text::body(self.status.service_active.clone()),
            ))
            .add(settings::item(
                "Autostart",
                text::body(self.status.service_enabled.clone()),
            ))
            .add(settings::item(
                "Compatible device",
                text::body(self.status.compatible_device_label.clone()),
            ))
            .add(settings::item(
                "Controller access",
                text::body(self.status.controller_status.clone()),
            ))
            .add(settings::item(
                "Detected USB devices",
                text::body(detected_devices),
            ))
            .add(settings::item(
                "Last log line",
                text::body(self.status.last_log_line.clone()),
            ));

        if let Some(error) = &self.status.last_error {
            content = content.add(settings::item("Last error", text::body(error.clone())));
        }

        let actions = widget::row()
            .spacing(8)
            .push(button::standard("Start").on_press(Message::StartService))
            .push(button::destructive("Stop").on_press(Message::StopService))
            .push(button::standard("Restart").on_press(Message::RestartService));

        let secondary_actions = widget::row()
            .spacing(8)
            .push(button::text("Recreate Mix Sinks").on_press(Message::RecreateMixSinks))
            .push(button::text("Refresh").on_press(Message::Refresh));

        let content = content
            .add(settings::item("Actions", actions))
            .add(settings::item("Tools", secondary_actions));

        self.core
            .applet
            .popup_container(widget::scrollable(content))
            .limits(
                Limits::NONE
                    .min_width(360.0)
                    .max_width(420.0)
                    .min_height(240.0)
                    .max_height(640.0),
            )
            .into()
    }

    fn style(&self) -> Option<theme::Style> {
        Some(cosmic::applet::style())
    }
}

impl Applet {
    fn run_service_action(&mut self, action: &str) {
        let result = Command::new("systemctl")
            .args(["--user", action, SERVICE_NAME])
            .output();

        self.status = AppStatus::gather();
        self.status.last_error = match result {
            Ok(output) if output.status.success() => None,
            Ok(output) => {
                let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
                if stderr.is_empty() {
                    Some(format!("systemctl {action} failed"))
                } else {
                    Some(stderr)
                }
            }
            Err(err) => Some(err.to_string()),
        };
    }
}

fn command_stdout<const N: usize>(program: &str, args: [&str; N]) -> Option<String> {
    let output = Command::new(program).args(args).output().ok()?;
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if !output.status.success() {
        warn!("{program} {:?} exited with {:?}", args, output.status.code());
    }
    (!stdout.is_empty()).then_some(stdout)
}

fn command_text<const N: usize>(program: &str, args: [&str; N]) -> Option<String> {
    let output = Command::new(program).args(args).output().ok()?;
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();

    if !output.status.success() {
        warn!("{program} {:?} exited with {:?}", args, output.status.code());
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
    let home = env::var("HOME").map_err(|err| err.to_string())?;
    let path = format!("{home}/.local/bin/nova7-virtualaudio");
    Command::new(path)
        .spawn()
        .map(|_| ())
        .map_err(|err| err.to_string())
}

fn main() -> cosmic::iced::Result {
    let env = env_logger::Env::default()
        .filter_or("RUST_LOG", "warn")
        .write_style_or("RUST_LOG_STYLE", "always");
    env_logger::init_from_env(env);

    cosmic::applet::run::<Applet>(())
}

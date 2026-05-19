use std::process::Command;
use tauri::Manager;

/// Start all SOS services using docker-compose
#[tauri::command]
async fn start_services() -> Result<String, String> {
    let output = Command::new("docker-compose")
        .args(["up", "-d"])
        .current_dir(get_sos_path())
        .output()
        .map_err(|e| format!("Failed to start services: {}", e))?;

    if output.status.success() {
        Ok("Services started successfully".to_string())
    } else {
        Err(String::from_utf8_lossy(&output.stderr).to_string())
    }
}

/// Stop all SOS services
#[tauri::command]
async fn stop_services() -> Result<String, String> {
    let output = Command::new("docker-compose")
        .args(["down"])
        .current_dir(get_sos_path())
        .output()
        .map_err(|e| format!("Failed to stop services: {}", e))?;

    if output.status.success() {
        Ok("Services stopped successfully".to_string())
    } else {
        Err(String::from_utf8_lossy(&output.stderr).to_string())
    }
}

/// Check health of a specific service
#[tauri::command]
async fn check_health(url: String) -> Result<bool, String> {
    let client = reqwest::Client::new();

    // Try /health endpoint first
    if let Ok(resp) = client.get(format!("{}/health", url)).send().await {
        if resp.status().is_success() {
            return Ok(true);
        }
    }

    // Try /v1/models for OpenAI-compatible servers
    if let Ok(resp) = client.get(format!("{}/v1/models", url)).send().await {
        if resp.status().is_success() {
            return Ok(true);
        }
    }

    Ok(false)
}

/// Get SOS project path
fn get_sos_path() -> String {
    // Try to find SOS path from environment or use default
    std::env::var("SOS_PATH").unwrap_or_else(|_| {
        dirs::home_dir()
            .map(|h| h.join("Development/Mumega/sos").to_string_lossy().to_string())
            .unwrap_or_else(|| ".".to_string())
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            start_services,
            stop_services,
            check_health,
        ])
        .setup(|app| {
            #[cfg(debug_assertions)]
            {
                let window = app.get_webview_window("main").unwrap();
                window.open_devtools();
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

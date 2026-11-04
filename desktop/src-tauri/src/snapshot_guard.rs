//! Validate supervisor data before operations that require reliable state.
use serde_json::Value;

fn require_section(snapshot: &Value, section: &str) -> Result<(), String> {
    if !snapshot.is_object() {
        return Err("Invalid supervisor snapshot".into());
    }
    // Older packaged runtimes do not include diagnostics. If present, enforce
    // the explicit section/message contract rather than interpreting bad data.
    if let Some(diagnostics) = snapshot.get("diagnostics") {
        let rows = diagnostics
            .as_array()
            .ok_or("Invalid snapshot diagnostics")?;
        for row in rows {
            let name = row
                .get("section")
                .and_then(Value::as_str)
                .ok_or("Invalid diagnostic section")?;
            let message = row
                .get("message")
                .and_then(Value::as_str)
                .ok_or("Invalid diagnostic message")?;
            if name == section {
                return Err(format!("Cannot read {section}: {message}"));
            }
        }
    }
    Ok(())
}

pub fn settings_for_write(snapshot: &Value) -> Result<Value, String> {
    require_section(snapshot, "settings")?;
    let settings = snapshot
        .get("settings")
        .filter(|v| v.is_object())
        .ok_or("Invalid settings snapshot")?;
    Ok(settings.clone())
}

pub fn has_active_instances(snapshot: &Value) -> Result<bool, String> {
    require_section(snapshot, "instances")?;
    let rows = snapshot
        .get("instances")
        .and_then(Value::as_array)
        .ok_or("Invalid instance snapshot")?;
    let mut active = false;
    for row in rows {
        match row.get("state").and_then(Value::as_str) {
            Some("running" | "starting" | "stopping" | "restarting") => active = true,
            Some("stopped" | "created" | "error" | "failed") => {}
            _ => return Err("Invalid instance state in snapshot".into()),
        }
    }
    Ok(active)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn failed_instance_section_is_not_an_empty_workspace() {
        let snapshot = json!({"instances": [], "diagnostics": [{"section":"instances", "message":"bad instance manifest"}]});
        assert!(has_active_instances(&snapshot)
            .unwrap_err()
            .contains("bad instance manifest"));
        for snapshot in [
            json!(null),
            json!({}),
            json!({"instances":null}),
            json!({"instances":{}}),
            json!({"instances":[{}]}),
            json!({"instances":[{"state":"unknown"}]}),
            json!({"instances":[],"diagnostics":null}),
        ] {
            assert!(has_active_instances(&snapshot).is_err(), "{snapshot}");
        }
    }
    #[test]
    fn settings_diagnostics_prevent_default_settings_writeback() {
        let snapshot = json!({"settings":{"autostart":false}, "diagnostics":[{"section":"settings","message":"invalid JSON"}]});
        assert!(settings_for_write(&snapshot)
            .unwrap_err()
            .contains("invalid JSON"));
        assert!(settings_for_write(&json!({"settings":null})).is_err());
    }
    #[test]
    fn settings_for_write_preserves_restore_bridges() {
        for restore_bridges in [false, true] {
            let settings = json!({"autostart":true, "restore_bridges":restore_bridges});
            assert_eq!(settings_for_write(&json!({"settings":settings.clone()})).unwrap(), settings);
        }
    }
    #[test]
    fn unrelated_section_failures_do_not_block_valid_operations() {
        for section in ["versions", "backups", "archives"] {
            let snapshot = json!({"instances":[], "settings":{"autostart":false}, "diagnostics":[{"section":section,"message":"broken entry"}]});
            assert!(!has_active_instances(&snapshot).unwrap());
            assert!(settings_for_write(&snapshot).is_ok());
        }
        for state in [
            "running",
            "starting",
            "stopping",
            "restarting",
            "stopped",
            "created",
            "error",
            "failed",
        ] {
            assert_eq!(
                has_active_instances(&json!({"instances":[{"state":state}]})).unwrap(),
                matches!(state, "running" | "starting" | "stopping" | "restarting")
            );
        }
    }
}

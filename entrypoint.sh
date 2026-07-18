#!/bin/sh
set -eu

require_secret_file() {
  variable_name="$1"
  secret_path="$2"
  if [ -z "$secret_path" ] || [ ! -r "$secret_path" ] || [ ! -s "$secret_path" ]; then
    echo "[truefan] required secret file is unavailable: $variable_name" >&2
    exit 1
  fi
}

component="${TRUEFAN_COMPONENT:-truefan-core}"
case "$component" in
  truefan-core)
    require_secret_file CONTROL_AGENT_TOKEN_FILE "${CONTROL_AGENT_TOKEN_FILE:-}"
    require_secret_file TRUEFAN_UI_WRITE_TOKEN_FILE "${TRUEFAN_UI_WRITE_TOKEN_FILE:-}"
    exec gunicorn --workers 2 --bind 0.0.0.0:5002 --chdir /opt/truefan/app server:app
    ;;
  truefan-control)
    require_secret_file TRUEFAN_AGENT_SECRET_FILE "${TRUEFAN_AGENT_SECRET_FILE:-}"
    if [ "${TRUEFAN_BACKEND:-hwmon_pwm}" = "ast2600_ipmi" ]; then
      require_secret_file BMC_USER_FILE "${BMC_USER_FILE:-}"
      require_secret_file BMC_PASSWORD_FILE "${BMC_PASSWORD_FILE:-}"
      require_secret_file TRUENAS_USER_FILE "${TRUENAS_USER_FILE:-}"
      require_secret_file TRUENAS_PASSWORD_FILE "${TRUENAS_PASSWORD_FILE:-}"
    fi
    exec uvicorn truefan_control.main:app --host 0.0.0.0 --port 5088
    ;;
  *)
    echo "[truefan] TRUEFAN_COMPONENT must be truefan-core or truefan-control" >&2
    exit 2
    ;;
esac

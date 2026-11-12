// Settings of the Worker: a copy of the sections of config/settings.yaml it uses, with the same
// names. Do not edit: this file is written by scripts/build_worker_settings.py, and
// tests/test_worker_settings.py fails when it is stale.

export const SETTINGS = deepFreeze({
  "survey": {
    "areas": [
      {
        "code": "operacoes",
        "label": {
          "pt": "Operações"
        }
      },
      {
        "code": "transporte",
        "label": {
          "pt": "Transporte"
        }
      },
      {
        "code": "atendimento",
        "label": {
          "pt": "Atendimento"
        }
      },
      {
        "code": "comercial",
        "label": {
          "pt": "Comercial"
        }
      },
      {
        "code": "tecnologia",
        "label": {
          "pt": "Tecnologia"
        }
      },
      {
        "code": "financeiro",
        "label": {
          "pt": "Financeiro"
        }
      },
      {
        "code": "pessoas",
        "label": {
          "pt": "Pessoas e Cultura"
        }
      },
      {
        "code": "juridico",
        "label": {
          "pt": "Jurídico"
        }
      }
    ],
    "tenure_bands": [
      "lt1",
      "y1to3",
      "y3to5",
      "gt5"
    ]
  },
  "privacy": {
    "min_group_size": 5,
    "public_retention_days": 30,
    "alert_log_retention_days": 90,
    "max_comment_chars": 1000,
    "display_excerpt_chars": 280
  },
  "queue": {
    "max_attempts": 2,
    "eta_window": 20
  },
  "limits": {
    "per_ip_window_s": 600,
    "per_ip_window_max": 3,
    "per_ip_day_max": 10,
    "global_hour_max": 60,
    "duplicate_window_hours": 24
  },
  "alerts": {
    "datasets": [
      "public"
    ],
    "channels": [],
    "immediate_per_hour": 3
  },
  "site": {
    "base_url": "https://voz-sem-nome.andreiludescher.com",
    "dashboard_path": {
      "pt": "/painel/",
      "en": "/en/dashboard/"
    }
  }
});

function deepFreeze(value) {
  if (value && typeof value === "object") {
    for (const item of Object.values(value)) deepFreeze(item);
    Object.freeze(value);
  }
  return value;
}

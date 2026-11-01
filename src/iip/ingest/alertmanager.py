"""Alertmanager webhook payload (version 4).

Reference: https://prometheus.io/docs/alerting/latest/configuration/#webhook_config

Validation is strict about the fields we depend on (status, fingerprint, startsAt)
and lenient about everything else (``extra="allow"``), because Alertmanager adds
fields over time and a new field must never make us drop alerts. The raw payload
of each alert is stored as well, so nothing is lost even if a field we ignore
today turns out to matter later.
"""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Upper bound per request. Alertmanager batches by group, so real payloads are far
# smaller; this only stops an oversized request from tying up the API.
MAX_ALERTS_PER_PAYLOAD = 1000

# Alertmanager sends endsAt = "0001-01-01T00:00:00Z" for alerts that haven't ended.
_ZERO_TIME_YEAR = 1

# Label keys that commonly name the affected service, in order of preference.
SERVICE_LABEL_KEYS = ("service", "service_name", "app", "job", "container")


class AlertmanagerAlert(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: Literal["firing", "resolved"]
    labels: dict[str, str] = Field(default_factory=dict[str, str])
    annotations: dict[str, str] = Field(default_factory=dict[str, str])
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime | None = Field(default=None, alias="endsAt")
    generator_url: str | None = Field(default=None, alias="generatorURL")
    fingerprint: str = Field(min_length=1, max_length=128)

    @field_validator("ends_at")
    @classmethod
    def _zero_time_means_unset(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.year <= _ZERO_TIME_YEAR:
            return None
        return value

    @field_validator("starts_at", "ends_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        # Alertmanager always sends RFC3339 with an offset. A naive timestamp would be
        # ambiguous, so treat it as UTC rather than guessing local time.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    @property
    def alertname(self) -> str:
        return self.labels.get("alertname", "unnamed")

    @property
    def service(self) -> str | None:
        for key in SERVICE_LABEL_KEYS:
            if self.labels.get(key):
                return self.labels[key]
        return None

    @property
    def severity(self) -> str | None:
        return self.labels.get("severity")

    @property
    def title(self) -> str:
        summary = self.annotations.get("summary")
        if summary:
            return summary
        return f"{self.alertname} on {self.service}" if self.service else self.alertname

    def raw(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


class AlertmanagerPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: str = "4"
    group_key: str | None = Field(default=None, alias="groupKey")
    status: Literal["firing", "resolved"]
    receiver: str | None = None
    alerts: list[AlertmanagerAlert] = Field(max_length=MAX_ALERTS_PER_PAYLOAD)

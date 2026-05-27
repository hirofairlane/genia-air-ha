"""Base entity for all Genia Air entities."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GeniaAirCoordinator


class GeniaAirEntity(CoordinatorEntity[GeniaAirCoordinator]):
    """Base class — all entities derive from this."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: GeniaAirCoordinator,
        *,
        unique_id: str,
        translation_key: str,
        circuit: str,
        msg: str,
        field_key: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._attr_translation_key = translation_key
        self._circuit = circuit
        self._msg = msg
        self._field_key = field_key  # "tempv", "0", "value", etc.

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            manufacturer="Vaillant",
            name="Genia Air",
            model="HMU 0901 / CTLS2 0509",  # TODO: detect from scan messages
            sw_version="0.1.0-alpha",
        )

    @property
    def _ebusd_value(self) -> Any:
        """Pluck the current value of our subscribed message+field from the coordinator."""
        msg_obj = self.coordinator.state.get((self._circuit, self._msg))
        if msg_obj is None:
            return None
        if self._field_key is None:
            return msg_obj.fields.get("value")
        return msg_obj.fields.get(self._field_key)

    @property
    def available(self) -> bool:
        """Available iff we have at least one MQTT payload for our message."""
        if not super().available:
            return False
        return (self._circuit, self._msg) in self.coordinator.state

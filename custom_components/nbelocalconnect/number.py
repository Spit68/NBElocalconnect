"""NBELocalConnect - Number platform for scan interval."""
import datetime
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from .const import DOMAIN
from logging import getLogger

_LOGGER = getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    coordinator = hass.data[DOMAIN][config_entry.entry_id + '_coordinator']
    entry_id = config_entry.entry_id

    async_add_entities([
        RTBScanIntervalNumber(coordinator, f'{entry_id}_v2_scan_interval')
    ])


class RTBScanIntervalNumber(CoordinatorEntity, NumberEntity, RestoreEntity):
    """Number entity to control the scan interval."""

    def __init__(self, coordinator, uid):
        super().__init__(coordinator)
        self.uid = uid
        self._value = 30.0

    async def async_added_to_hass(self):
        """Restore last value on HA restart."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in ('unknown', 'unavailable'):
            try:
                restored = float(last_state.state)
                self._value = restored
                self.coordinator.update_interval = datetime.timedelta(seconds=int(restored))
                _LOGGER.info(f"Restored scan interval to {restored}s")
            except (ValueError, TypeError):
                pass

    @property
    def name(self):
        return "Scan Interval"

    @property
    def unique_id(self):
        return self.uid

    @property
    def native_value(self):
        return self._value

    @property
    def native_min_value(self):
        return 10.0

    @property
    def native_max_value(self):
        return 300.0

    @property
    def native_step(self):
        return 5.0

    @property
    def native_unit_of_measurement(self):
        return "s"

    @property
    def mode(self):
        return NumberMode.BOX

    async def async_set_native_value(self, value: float) -> None:
        """Update scan interval."""
        self._value = value
        self.coordinator.update_interval = datetime.timedelta(seconds=int(value))
        _LOGGER.info(f"Scan interval changed to {int(value)}s")
        self.async_write_ha_state()

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

    @property
    def entity_registry_enabled_default(self):
        return True

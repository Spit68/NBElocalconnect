from homeassistant.core import callback
from homeassistant.helpers.entity import Entity
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN
from .protocol import Proxy
from logging import getLogger
import asyncio

_LOGGER = getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    coordinator = hass.data[DOMAIN][config_entry.entry_id + '_coordinator']
    proxy = coordinator.proxy
    entry_id = config_entry.entry_id

    async_add_entities([
        RTBSignalButton(coordinator, proxy, "Start Boiler",       "settings/misc/start",       f"{entry_id}_nbestart", "1"),
        RTBSignalButton(coordinator, proxy, "Stop Boiler",        "settings/misc/stop",        f"{entry_id}_nbestop",  "1"),
        RTBSignalButton(coordinator, proxy, "Reset Boiler Alarm", "settings/misc/reset_alarm", f"{entry_id}_nbereset", "1"),
        NBEBackupButton(coordinator, hass, f"{entry_id}_nbe_backup"),
        NBERestoreButton(coordinator, hass, f"{entry_id}_nbe_restore"),
        NBEDeleteBackupButton(coordinator, hass, f"{entry_id}_nbe_delete_backup"),
    ])


class RTBSignalButton(CoordinatorEntity, ButtonEntity):
    """Representation of a signal switch."""

    def __init__(self, coordinator, proxy, name, path, uid, value):
        super().__init__(coordinator)
        self._name = name
        self.proxy = proxy
        self._path = path
        self._value = value
        self.uid = uid

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self.uid

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

    async def async_press(self) -> None:
        _LOGGER.debug(f"Pressing {self._name}...")
        def _do_set():
            with self.coordinator.proxy_lock:
                self.proxy.set(self._path, self._value)
        try:
            await self.hass.async_add_executor_job(_do_set)
            _LOGGER.debug(f"Successfully pressed {self._name}")
        except Exception as e:
            _LOGGER.error(f"Error pressing {self._name}: {e}")
            raise
        await asyncio.sleep(3)
        await self.coordinator.async_request_refresh()


class NBEBackupButton(CoordinatorEntity, ButtonEntity):
    """Knap der starter en backup af boiler settings."""

    def __init__(self, coordinator, hass, uid):
        super().__init__(coordinator)
        self._hass = hass
        self.uid = uid

    @property
    def name(self):
        return "Backup Settings"

    @property
    def unique_id(self):
        return self.uid

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

    @property
    def icon(self):
        return "mdi:content-save"

    async def async_press(self) -> None:
        await self._hass.services.async_call(DOMAIN, "backup_settings", {})


class NBERestoreButton(CoordinatorEntity, ButtonEntity):
    """Knap der gendanner boiler settings fra valgt backup fil."""

    def __init__(self, coordinator, hass, uid):
        super().__init__(coordinator)
        self._hass = hass
        self.uid = uid

    @property
    def name(self):
        return "Restore Settings"

    @property
    def unique_id(self):
        return self.uid

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

    @property
    def icon(self):
        return "mdi:backup-restore"

    async def async_press(self) -> None:
        await self._hass.services.async_call(DOMAIN, "restore_settings", {})

class NBEDeleteBackupButton(CoordinatorEntity, ButtonEntity):
    """Button that deletes the selected backup file."""

    def __init__(self, coordinator, hass, uid):
        super().__init__(coordinator)
        self._hass = hass
        self.uid = uid

    @property
    def name(self):
        return "Delete Backup"

    @property
    def unique_id(self):
        return self.uid

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

    @property
    def icon(self):
        return "mdi:delete"

    async def async_press(self) -> None:
        await self._hass.services.async_call(DOMAIN, "delete_backup", {})
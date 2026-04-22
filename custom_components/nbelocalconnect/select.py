"""NBELocalConnect - Select platform for backup file selection."""
import os
from homeassistant.components.select import SelectEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, NBE_BACKUP_DIR
from logging import getLogger

_LOGGER = getLogger(__name__)

NO_BACKUPS = "(no backups)"


async def async_setup_entry(hass, config_entry, async_add_entities):
    coordinator = hass.data[DOMAIN][config_entry.entry_id + '_coordinator']
    entry_id = config_entry.entry_id

    select_entity = NBEBackupSelectEntity(coordinator, hass, f"{entry_id}_backup_select")
    async_add_entities([select_entity])

    # Store reference so backup service can refresh after new backup
    hass.data[DOMAIN][entry_id + '_backup_select'] = select_entity


class NBEBackupSelectEntity(CoordinatorEntity, SelectEntity):
    """Select entity that lists available NBE backup files."""

    def __init__(self, coordinator, hass, uid):
        super().__init__(coordinator)
        self._hass = hass
        self.uid = uid
        self._current_option = NO_BACKUPS
        self._options = [NO_BACKUPS]
        # No file system access in __init__ - done in async_added_to_hass

    def _get_backup_files_sync(self):
        """Get sorted list of .json backup files - newest first. Blocking - run in executor."""
        if not os.path.exists(NBE_BACKUP_DIR):
            return []
        try:
            files = [f for f in os.listdir(NBE_BACKUP_DIR) if f.endswith('.json')]
            files.sort(
                key=lambda f: os.path.getmtime(os.path.join(NBE_BACKUP_DIR, f)),
                reverse=True
            )
            return files
        except Exception as e:
            _LOGGER.error(f"Error reading backup directory: {e}")
            return []

    async def _async_get_backup_files(self):
        """Get backup files via executor to avoid blocking event loop."""
        return await self._hass.async_add_executor_job(self._get_backup_files_sync)

    async def _async_refresh_options(self, select_newest=False):
        """Refresh options list from disk."""
        files = await self._async_get_backup_files()
        if files:
            self._options = files
            if select_newest or self._current_option not in files:
                self._current_option = files[0]
        else:
            self._options = [NO_BACKUPS]
            self._current_option = NO_BACKUPS
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        """Load file list when entity is added."""
        await super().async_added_to_hass()
        await self._async_refresh_options()

    def _handle_coordinator_update(self):
        """Refresh file list on every coordinator update."""
        self._hass.async_create_task(self._async_refresh_options())

    @property
    def name(self):
        return "Restore — choose backup file"

    @property
    def unique_id(self):
        return self.uid

    @property
    def icon(self):
        return "mdi:backup-restore"

    @property
    def options(self):
        return self._options

    @property
    def current_option(self):
        return self._current_option

    async def async_select_option(self, option: str) -> None:
        """User selects a file in the dropdown."""
        self._current_option = option
        self.async_write_ha_state()

    async def async_refresh_options(self):
        """Refresh file list - called after a new backup is saved."""
        await self._async_refresh_options(select_newest=True)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

    @property
    def entity_registry_enabled_default(self):
        return True
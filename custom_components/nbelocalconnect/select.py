"""NBELocalConnect - Select platform for backup file and boiler settings."""
import os
from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, NBE_BACKUP_DIR
from logging import getLogger

_LOGGER = getLogger(__name__)

NO_BACKUPS = "(no backups)"

# ============================================================
# OPTION MAPS  (label → boiler value)
# ============================================================

ALL_L_OPTIONS = {
    "Disabled": 0,
    "L5": 5, "L6": 6, "L7": 7, "L8": 8, "L9": 9,
    "L10": 10, "L11": 11, "L12": 12, "L13": 13,
}

ALL_T_OPTIONS = {
    "Disabled": 0,
    "T1": 1, "T2": 2, "T3": 3, "T4": 4, "T5": 5,
    "T6": 6, "T7": 7, "T8": 8, "T9": 9, "T10": 10,
}

ALL_T_WITH_WWW_OPTIONS = {
    "WWW temp.": 0,
    **{
        label: value
        for label, value in ALL_T_OPTIONS.items()
        if value != 0
    }
}

# All keys using L-numbers (used for 'in use' filtering)
SETTINGS_L_KEYS = {
    'settings/cleaning/output_ash',
    'settings/cleaning/output_burner',
    'settings/cleaning/output_boiler1',
    'settings/cleaning/output_boiler2',
    'settings/fan/output_exhaust',
    'settings/pump/output',
    'settings/alarm/output',
    'settings/hot_water/output',
    'settings/weather/output_pump',
    'settings/weather2/output_pump',
    'settings/weather/output_down',
    'settings/weather2/output_down',
    'settings/weather/output_up',
    'settings/weather2/output_up',
    'settings/sun/output_pump',
    'settings/sun/output_excess',
}

# All keys using T-numbers
SETTINGS_T_KEYS = {
    'settings/weather/input_reference',
    'settings/weather2/input_reference',
    'settings/weather/input_forward',
    'settings/weather2/input_forward',
    'settings/sun/input_excess',
    'settings/sun/input_dhw',
    'settings/sun/input_collector',
    'settings/sun/input_collector_2',    
}

# Specific dropdowns with fixed options
SPECIFIC_OPTIONS = {
    'settings/oxygen/regulation': {
        "Off": 0,
        "Show O2% only": 1,
        "On": 2,
    },
    'settings/oxygen/lambda_type': {
        "Bosch": 0,
        "NTK": 1,
        "Denso": 2,
    },
    'settings/hopper/distance_sensor': {
        "Off": 0,
        "Ultrasound": 1,
        "Infrared (20-150cm)": 2,
    },
    'settings/cleaning/pressure_t7': {
        # Unknown - feedback needed. Placeholder.
        "0": 0,
        "1": 1,
        "2": 2,
    },
    'settings/hot_water/dwh_weather': {
        "0%": 0,
        "100%": 1,
        "Regulation": 2,
        "Standby": 3,
    },
    'settings/hot_water/dwh_weather2': {
        "0%": 0,
        "100%": 1,
        "Regulation": 2,
        "Standby": 3,
    },    
}

# All settings select keys
ALL_SETTINGS_SELECT_KEYS = (
    SETTINGS_L_KEYS
    | SETTINGS_T_KEYS
    | set(SPECIFIC_OPTIONS.keys())
)

# Keys enabled by default
DEFAULT_ENABLED_SELECT_KEYS = {
    'settings/alarm/output',
    'settings/pump/output',
    'settings/hot_water/output',
    'settings/oxygen/regulation',
    'settings/hopper/distance_sensor',
}


# Name overrides for specific keys
SETTINGS_NAME_OVERRIDES = {
    'settings/pump/output': 'Boiler Pump Output',
    'settings/hot_water/output': 'DHW Valve Output',
    'settings/alarm/output': 'Alarm Output',
    'settings/weather/output_down': 'Weather Valve Output Closed',
    'settings/weather/output_up': 'Weather Valve Output Open',
    'settings/weather2/output_down': 'Weather2 Valve Output Closed',
    'settings/weather2/output_up': 'Weather2 Valve Output Open',    
}


def _key_to_name(key: str) -> str:
    """Get friendly name for a settings key."""
    if key in SETTINGS_NAME_OVERRIDES:
        return SETTINGS_NAME_OVERRIDES[key]
    parts = key.replace('settings/', '').split('/')
    if len(parts) == 2:
        return f"{parts[0].replace('_', ' ').title()} {parts[1].replace('_', ' ').title()}"
    return key.replace('settings/', '').replace('_', ' ').replace('/', ' ').title()


def _get_option_type(key: str) -> str:
    """Returns 'L', 'T', 'T_WWW' or 'specific'."""
    if key in SETTINGS_L_KEYS:
        return 'L'
    if key in ('settings/weather/input_reference', 'settings/weather2/input_reference'):
        return 'T_WWW'
    if key in SETTINGS_T_KEYS:
        return 'T'
    return 'specific'


def _get_base_option_map(key: str) -> dict:
    """Get base option map for a key."""
    opt_type = _get_option_type(key)
    if opt_type == 'L':
        return ALL_L_OPTIONS
    if opt_type == 'T_WWW':
        return ALL_T_WITH_WWW_OPTIONS
    if opt_type == 'T':
        return ALL_T_OPTIONS
    return SPECIFIC_OPTIONS.get(key, {})


# ============================================================
# SETUP
# ============================================================

async def async_setup_entry(hass, config_entry, async_add_entities):
    coordinator = hass.data[DOMAIN][config_entry.entry_id + '_coordinator']
    entry_id = config_entry.entry_id

    entities = []

    # --- Backup select ---
    backup_select = NBEBackupSelectEntity(coordinator, hass, f"{entry_id}_backup_select")
    entities.append(backup_select)
    hass.data[DOMAIN][entry_id + '_backup_select'] = backup_select

    # --- Settings select entities ---
    all_keys = coordinator.rtbdata.get_all_keys()
    for key in all_keys:
        if key not in ALL_SETTINGS_SELECT_KEYS:
            continue

        option_map = _get_base_option_map(key)
        if not option_map:
            continue

        name = SETTINGS_NAME_OVERRIDES.get(key)
        if not name:
            parts = key.replace('settings/', '').split('/')
            if len(parts) == 2:
                category, item = parts
                name = f"{category.replace('_', ' ').title()} {item.replace('_', ' ').title()}"
            else:
                name = key.replace('settings/', '').replace('_', ' ').replace('/', ' ').title()

        uid = f"{entry_id}_v2_sel_{key.replace('/', '_')}"
        entities.append(
            NBESettingsSelect(coordinator, hass, name, key, uid, option_map)
        )
        _LOGGER.debug(f"Created settings select: {name} ({key})")

    _LOGGER.info(f"✓ Select entities created: {len(entities)}")
    async_add_entities(entities)


# ============================================================
# BACKUP SELECT
# ============================================================

class NBEBackupSelectEntity(CoordinatorEntity, SelectEntity):
    """Select entity that lists available NBE backup files."""

    def __init__(self, coordinator, hass, uid):
        super().__init__(coordinator)
        self._hass = hass
        self.uid = uid
        self._current_option = NO_BACKUPS
        self._options = [NO_BACKUPS]

    def _get_backup_files_sync(self):
        if not os.path.exists(NBE_BACKUP_DIR):
            return []
        try:
            files = [f for f in os.listdir(NBE_BACKUP_DIR) if f.endswith('.json')]
            files.sort(key=lambda f: os.path.getmtime(os.path.join(NBE_BACKUP_DIR, f)), reverse=True)
            return files
        except Exception as e:
            _LOGGER.error(f"Error reading backup directory: {e}")
            return []

    async def _async_get_backup_files(self):
        return await self._hass.async_add_executor_job(self._get_backup_files_sync)

    async def _async_refresh_options(self, select_newest=False):
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
        await super().async_added_to_hass()
        await self._async_refresh_options()

    def _handle_coordinator_update(self):
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
        self._current_option = option
        self.async_write_ha_state()

    async def async_refresh_options(self):
        await self._async_refresh_options(select_newest=True)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

    @property
    def entity_registry_enabled_default(self):
        return True


# ============================================================
# SETTINGS SELECT
# ============================================================

class NBESettingsSelect(CoordinatorEntity, SelectEntity):
    """Select entity for a boiler setting with discrete options."""

    def __init__(self, coordinator, hass, name, client_key, uid, base_option_map):
        super().__init__(coordinator)
        self._hass = hass
        self._name = name
        self.client_key = client_key
        self.uid = uid
        self._base_option_map = base_option_map  # {label: boilervalue}
        self._option_type = _get_option_type(client_key)
        self._pending_option = None  # Snap-back fix

    def _get_own_value(self) -> int | None:
        """Get boiler value for this entity."""
        raw = self.coordinator.rtbdata.get(self.client_key)
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return None

    def _get_used_by(self) -> dict[int, list[str]]:
        """Get dict of value -> entity names for all used L/T assignments."""
        if self._option_type not in ("L", "T", "T_WWW"):
            return {}

        all_keys = SETTINGS_L_KEYS if self._option_type == "L" else SETTINGS_T_KEYS
        used_by: dict[int, list[str]] = {}

        for key in all_keys:
            raw = self.coordinator.rtbdata.get(key)

            try:
                value = int(float(raw))
            except (TypeError, ValueError):
                continue

            if value == 0:
                continue

            name = _key_to_name(key)

            used_by.setdefault(value, [])

            if name not in used_by[value]:
                used_by[value].append(name)

        return used_by

    @property
    def options(self) -> list[str]:
        """Return all options. Used L/T assignments show entity name suffix."""
        used_by = self._get_used_by()
        result = []

        for label, val in self._base_option_map.items():
            names = used_by.get(val)

            if names:
                result.append(f"{label} ({' + '.join(names)})")
            else:
                result.append(label)

        return result

    @property
    def current_option(self) -> str | None:
        if self._pending_option is not None:
            return self._pending_option
        own_val = self._get_own_value()
        if own_val is None:
            return None
        # Return full option string from options list (may include "(entity name)" suffix)
        for opt in self.options:
            base = opt.split(" (")[0].strip()
            opt_val = self._base_option_map.get(base)
            if opt_val == own_val:
                return opt
        return None

    def _handle_coordinator_update(self) -> None:
        """Clear pending option when coordinator updates."""
        self._pending_option = None
        super()._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        # Strip "(entity name)" suffix if present
        base_option = option.split(" (")[0].strip()

        val = self._base_option_map.get(base_option)

        if val is None:
            _LOGGER.error(f"Unknown option '{option}' for {self.client_key}")
            return

        if self._option_type == "L":
            used_by = self._get_used_by()
            users = used_by.get(val, [])

            own_name = _key_to_name(self.client_key)
            other_users = [name for name in users if name != own_name]

            if other_users:
                message = (
                    f"Cannot set {self.name} to {base_option}.\n\n"
                    f"{base_option} is already used by:\n"
                    f"{', '.join(other_users)}"
                )

                _LOGGER.warning(message)

                await self._hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "title": "NBE Output Already In Use",
                        "message": message,
                    },
                )

                self._pending_option = None
                self.async_write_ha_state()
                return
                
        self._pending_option = option
        self.async_write_ha_state()                

        await self._hass.services.async_call(
            DOMAIN,
            "set_setting",
            {"key": self.client_key, "value": val},
        )

        _LOGGER.info(f"Set {self.client_key} = {val} ({option})")

        # Optimistic update: update rtbdata immediately so all dropdowns refresh at once
        self.coordinator.rtbdata.data[self.client_key] = str(val)
        self.coordinator.async_update_listeners()


    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self.uid

    @property
    def entity_category(self):
        return EntityCategory.CONFIG

    @property
    def extra_state_attributes(self):
        attrs = {
            "datapoint_path": self.client_key,
            "writable": True,
        }
        return attrs

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

    @property
    def entity_registry_enabled_default(self):
        return self.client_key in DEFAULT_ENABLED_SELECT_KEYS
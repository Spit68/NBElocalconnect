import asyncio
import re
import datetime
import threading
import json
import os
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, EVENT_CORE_CONFIG_UPDATE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from .const import DOMAIN, NBE_BACKUP_DIR
from .rtbdata import RTBData
from .protocol import Proxy
from logging import getLogger

logger = getLogger(__name__)

# Alle settings der gemmes og gendannes ved backup/restore.
# Format: (kategori, key) — svarer til settings/<kategori>/<key>
BACKUP_SETTINGS = [
    # boiler
    ("boiler", "temp"),
    ("boiler", "diff_over"),
    ("boiler", "diff_under"),
    ("boiler", "min_return"),
    ("boiler", "reduction"),
    ("boiler", "ext_stop_temp"),
    ("boiler", "ext_stop_diff"),
    ("boiler", "ext_switch"),
    ("boiler", "ext_off_delay"),
    ("boiler", "ext_on_delay"),
    # alarm
    ("alarm", "output"),
    ("alarm", "min_boiler_temp"),
    ("alarm", "max_shaft_temp"),
    # oxygen
    ("oxygen", "regulation"),
    ("oxygen", "o2_low"),
    ("oxygen", "o2_medium"),
    ("oxygen", "o2_high"),
    ("oxygen", "calibration_number"),
    ("oxygen", "block_time"),
    ("oxygen", "regulation_time"),
    ("oxygen", "fan_gain_p"),
    ("oxygen", "fan_gain_i"),
    ("oxygen", "corr_fan_10"),
    ("oxygen", "corr_fan_50"),
    ("oxygen", "corr_fan_100"),
    ("oxygen", "pellets_gain_p"),
    ("oxygen", "pellets_gain_i"),
    ("oxygen", "lambda_type"),
    ("oxygen", "lambda_expansion_module"),
    # cleaning
    ("cleaning", "fan_period"),
    ("cleaning", "fan_time"),
    ("cleaning", "fan_speed"),
    ("cleaning", "comp_period"),
    ("cleaning", "valve_period"),
    ("cleaning", "valve_time"),
    ("cleaning", "pellets_stop"),
    ("cleaning", "comp_fan_speed"),
    ("cleaning", "output_ash"),
    ("cleaning", "output_burner"),
    ("cleaning", "output_boiler1"),
    ("cleaning", "output_boiler2"),
    # regulation
    ("regulation", "boiler_gain_p"),
    ("regulation", "boiler_gain_i"),
    ("regulation", "power_per_minute"),
    ("regulation", "boiler_power_min"),
    ("regulation", "boiler_power_max"),
    ("regulation", "dhw_gain_p"),
    ("regulation", "dhw_gain_i"),
    ("regulation", "dhw_setpoint_addition"),
    ("regulation", "dhw_power_min"),
    ("regulation", "dhw_power_max"),
    # fan
    ("fan", "speed_10"),
    ("fan", "speed_50"),
    ("fan", "speed_100"),
    ("fan", "use_fan_rpm"),
    ("fan", "alarm_fan_rpm"),
    ("fan", "alarm_fan_current"),
    ("fan", "exhaust_10"),
    ("fan", "exhaust_50"),
    ("fan", "exhaust_100"),
    # ignition
    ("ignition", "pellets"),
    ("ignition", "power"),
    ("ignition", "fan_10"),
    ("ignition", "fan_50"),
    ("ignition", "fan_100"),
    ("ignition", "max_time"),
    ("ignition", "preheat_time"),
    ("ignition", "exhaust_speed"),
    # pump
    ("pump", "start_temp_run"),
    ("pump", "start_temp_idle"),
    ("pump", "flow_liters"),
    ("pump", "flow_freq"),
    ("pump", "output"),
    # hopper
    ("hopper", "auger_capacity"),
    ("hopper", "auger_consumption"),
    ("hopper", "auto_fill"),
    ("hopper", "distance_max"),
    ("hopper", "distance_sensor"),
    # auger
    ("auger", "kw_min"),
    ("auger", "kw_max"),
    ("auger", "min_dose"),
    # hot_water
    ("hot_water", "temp"),
    ("hot_water", "diff_under"),
    ("hot_water", "dhw_remain"),
    ("hot_water", "output"),
    # sun
    ("sun", "collector_temp"),
    ("sun", "pump_start_diff"),
    ("sun", "pump_stop_diff"),
    ("sun", "pump_min_speed"),
    ("sun", "dhw_max_temp"),
    ("sun", "flow_liters"),
    ("sun", "excess_from_top"),
    ("sun", "output_pump"),
    ("sun", "output_excess"),
    ("sun", "input_collector"),
    ("sun", "input_collector_2"),
    ("sun", "input_dhw"),
    ("sun", "input_excess"),
]


def load_translations(language: str) -> dict:
    """Load translation file for given language, fallback to en."""
    translations_dir = os.path.join(os.path.dirname(__file__), "translations")
    lang_file = os.path.join(translations_dir, f"{language}.json")

    if not os.path.exists(lang_file):
        logger.debug(f"No translation file for '{language}', falling back to en")
        lang_file = os.path.join(translations_dir, "en.json")

    try:
        with open(lang_file, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading translations from {lang_file}: {e}")
        return {"boiler_state": {}, "boiler_substate": {}, "boiler_info": {}}


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the NBE component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass, entry):
    """Set up NBE from a config entry."""
    logger.info("Setting up NBELocalConnect integration...")

    # Ryd op i orphaned entities fra tidligere installationer.
    from homeassistant.helpers import entity_registry as er
    ent_reg = er.async_get(hass)
    active_entry_ids = {e.entry_id for e in hass.config_entries.async_entries(DOMAIN)}
    orphaned = [
        e for e in ent_reg.entities.values()
        if e.platform == DOMAIN and e.config_entry_id not in active_entry_ids
    ]
    for orphan in orphaned:
        ent_reg.async_remove(orphan.entity_id)
        logger.info(f"Removed orphaned entity: {orphan.entity_id} (uid: {orphan.unique_id})")
    if orphaned:
        logger.info(f"Cleaned up {len(orphaned)} orphaned entities from previous installs")

    # Get configuration
    ip_address = entry.data.get('ip_address')
    password = entry.data.get(CONF_PASSWORD)
    port = entry.data.get('port', 8483)
    serialnumber = entry.data.get('serial', None)
    scan_interval = entry.data.get(CONF_SCAN_INTERVAL, 30)

    if serialnumber:
        ip_address = '<broadcast>'

    logger.info(f"Creating proxy connection to {ip_address}:{port}...")
    try:
        proxy = await hass.async_add_executor_job(
            Proxy,
            password,
            port,
            ip_address,
            serialnumber
        )
        logger.info("✅ Proxy connection established!")
    except Exception as e:
        logger.error(f"❌ Failed to create proxy connection: {e}", exc_info=True)
        return False

    # Create device
    device_registry = dr.async_get(hass)
    device_identifier = proxy.serial if hasattr(proxy, 'serial') and proxy.serial else ip_address
    device_name = f"NBE Boiler {device_identifier}"
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=device_name,
        manufacturer="NBE",
        model="Pellet Boiler",
    )

    # threading.Lock — beskytter socketen mod samtidige kald fra coordinator og services
    proxy_lock = threading.Lock()

    # Create coordinator
    coordinator = RTBDataCoordinator(
        hass,
        entry.entry_id,
        proxy,
        scan_interval,
        proxy_lock
    )

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator
    hass.data[DOMAIN][entry.entry_id + '_coordinator'] = coordinator

    # Setup platforms
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "button", "number", "select"])

    # Listen for language changes
    async def handle_language_change(event):
        """Reload translations when HA language changes."""
        new_language = hass.config.language
        logger.info(f"Language changed to '{new_language}', reloading translations...")
        coordinator.translations = load_translations(new_language)
        coordinator.async_update_listeners()

    entry.async_on_unload(
        hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, handle_language_change)
    )

    # ----------------------------------------------------------------
    # Service: set_setting
    # Uses same proxy_lock as coordinator - no socket conflicts
    # ----------------------------------------------------------------
    async def handle_set_setting(call):
        """Handle set_setting service call."""
        entity_id = call.data.get("entity_id")
        key = call.data.get("key")
        value = call.data.get("value")

        # Hvis entity_id givet, find datapoint fra entity attributes
        if entity_id and not key:
            state = hass.states.get(entity_id)
            if state and state.attributes:
                key = state.attributes.get("datapoint_path")
                is_writable = state.attributes.get("writable", False)

                if not is_writable:
                    raise HomeAssistantError(
                        f"{entity_id} is read-only and cannot be changed. "
                        f"Please select a boiler setting sensor instead."
                    )

            if not key:
                raise HomeAssistantError(f"Could not find datapoint_path for {entity_id}")

        if not key:
            raise HomeAssistantError("Must provide either entity_id or key parameter")

        logger.info(f"Setting {key} to {value}")

        def locked_set(k, v):
            with proxy_lock:
                return proxy.set(k, v)

        try:
            result = await hass.async_add_executor_job(locked_set, key, str(value))
            logger.info(f"Successfully set {key} = {value}")
        except Exception as e:
            logger.error(f"Error setting {key}: {e}")
            raise

    hass.services.async_register(DOMAIN, "set_setting", handle_set_setting)

    # ----------------------------------------------------------------
    # Service: backup_settings
    # Saves all settings to a JSON file in /config/nbe_backup/
    # Valgfri parameter: name — bruges som præfiks i filnavnet
    # ----------------------------------------------------------------
    async def handle_backup_settings(call):
        """Save all boiler settings to backup file."""
        timestamp = datetime.datetime.now().strftime("%d-%m-%Y-%H-%M")

        # Find highest backup number and create dir - run in executor
        def get_next_num_and_makedirs():
            os.makedirs(NBE_BACKUP_DIR, exist_ok=True)
            next_num = 1
            nums = []
            for fname in os.listdir(NBE_BACKUP_DIR):
                m = re.match(r'backup(\d+)_', fname)
                if m:
                    nums.append(int(m.group(1)))
            if nums:
                next_num = max(nums) + 1
            return next_num

        next_num = await hass.async_add_executor_job(get_next_num_and_makedirs)
        filename = f"backup{next_num}_{timestamp}.json"
        filepath = os.path.join(NBE_BACKUP_DIR, filename)

        # Fetch all settings directly from boiler
        # Use all unique categories from BACKUP_SETTINGS
        categories = list(dict.fromkeys(cat for cat, key in BACKUP_SETTINGS))

        def locked_get_all():
            results = {}
            with proxy_lock:
                for category in categories:
                    try:
                        data = proxy.get(f"settings/{category}/")
                        for item in (data or []):
                            s = str(item)
                            if "=" in s and s.startswith("settings/"):
                                path, value = s.rsplit("=", 1)
                                results[path] = value
                    except Exception as e:
                        logger.warning(f"Backup: could not fetch settings/{category}/: {e}")
            return results

        settings_lookup = await hass.async_add_executor_job(locked_get_all)

        backup_data = {
            "version": 1,
            "created": datetime.datetime.now().isoformat(),
            "name": filename,
            "settings": {}
        }

        missing = []
        for category, key in BACKUP_SETTINGS:
            path = f"settings/{category}/{key}"
            if path in settings_lookup:
                backup_data["settings"][path] = settings_lookup[path]
            else:
                missing.append(path)
                logger.warning(f"Backup: {path} not found in cached data")

        def write_backup():
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(backup_data, f, indent=2, ensure_ascii=False)

        await hass.async_add_executor_job(write_backup)
        logger.info(f"✅ Backup saved: {filepath} ({len(backup_data['settings'])} settings)")

        # Update select entity with the new file
        select_key = entry.entry_id + '_backup_select'
        if select_key in hass.data[DOMAIN]:
            await hass.data[DOMAIN][select_key].async_refresh_options()

        msg = f"NBE backup saved: **{filename}**\n{len(backup_data['settings'])} settings saved."
        if missing:
            msg += f"\nℹ️ {len(missing)} settings not available on this boiler."
        await hass.services.async_call("persistent_notification", "create", {"message": msg, "title": "NBE Backup", "notification_id": "nbe_backup"})

    hass.services.async_register(DOMAIN, "backup_settings", handle_backup_settings)

    # ----------------------------------------------------------------
    # Service: restore_settings
    # Restores settings from the file selected in select entity
    # ----------------------------------------------------------------
    async def handle_restore_settings(call):
        """Restore boiler settings from selected backup file."""
        select_key = entry.entry_id + '_backup_select'
        select_entity = hass.data[DOMAIN].get(select_key)

        if select_entity is None:
            raise HomeAssistantError("Backup select entity not found")

        filename = select_entity.current_option
        if not filename or filename == "(no backups)":
            await hass.services.async_call("persistent_notification", "create", {"message": "No backup file selected - please choose a file in the Restore dropdown first.", "title": "NBE Restore", "notification_id": "nbe_restore_error"})
            return

        filepath = os.path.join(NBE_BACKUP_DIR, filename)

        def read_backup():
            if not os.path.exists(filepath):
                return None
            with open(filepath, encoding="utf-8") as f:
                return json.load(f)

        backup_data = await hass.async_add_executor_job(read_backup)
        if backup_data is None:
            raise HomeAssistantError(f"Backup file not found: {filepath}")

        settings = backup_data.get("settings", {})
        if not settings:
            raise HomeAssistantError(f"Backup file contains no settings: {filename}")

        def locked_set(k, v):
            with proxy_lock:
                return proxy.set(k, str(v))

        ok = 0
        errors = 0
        total = len(settings)

        # Send start notification
        await hass.services.async_call("persistent_notification", "create", {
            "message": f"Restoring **{filename}**...\nThis will take a few minutes. Progress updates every 10 settings.",
            "title": "NBE Restore",
            "notification_id": "nbe_restore"
        })

        for path, value in settings.items():
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await hass.async_add_executor_job(locked_set, path, value)
                    ok += 1
                    if attempt > 0:
                        logger.info(f"Restore retry succeeded for {path} (attempt {attempt + 1})")
                    break
                except OSError as e:
                    # Timeout - retry after short pause
                    if attempt < max_retries - 1:
                        logger.warning(f"Restore timeout for {path}, retrying ({attempt + 1}/{max_retries - 1})...")
                        await asyncio.sleep(1)
                    else:
                        logger.error(f"Restore error for {path}: {e}")
                        errors += 1
                except Exception as e:
                    # Other errors (index out of range etc) - no point retrying
                    logger.error(f"Restore error for {path}: {e}")
                    errors += 1
                    break

            # Update progress notification every 10 settings
            if (ok + errors) % 10 == 0:
                await hass.services.async_call("persistent_notification", "create", {
                    "message": f"Restoring... {ok + errors} of {total} settings done.",
                    "title": "NBE Restore",
                    "notification_id": "nbe_restore"
                })

        logger.info(f"✅ Restore complete from {filename}: {ok} ok, {errors} errors")

        # Send final notification before coordinator refresh
        msg = f"NBE restore from **{filename}** complete.\n✅ {ok} settings restored."
        if errors:
            msg += f"\n❌ {errors} settings failed - check log."
        await hass.services.async_call("persistent_notification", "create", {"message": msg, "title": "NBE Restore", "notification_id": "nbe_restore"})

        # Refresh coordinator data
        await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, "restore_settings", handle_restore_settings)

    # ----------------------------------------------------------------
    # Service: delete_backup
    # Deletes the file currently selected in the select entity
    # ----------------------------------------------------------------
    async def handle_delete_backup(call):
        """Delete selected backup file."""
        select_key = entry.entry_id + '_backup_select'
        select_entity = hass.data[DOMAIN].get(select_key)

        if select_entity is None:
            raise HomeAssistantError("Backup select entity not found")

        filename = select_entity.current_option
        if not filename or filename == "(no backups)":
            await hass.services.async_call("persistent_notification", "create", {
                "message": "No backup file selected - please choose a file in the Restore dropdown first.",
                "title": "NBE Delete Backup",
                "notification_id": "nbe_delete_error"
            })
            return

        filepath = os.path.join(NBE_BACKUP_DIR, filename)
        def check_and_delete():
            if not os.path.exists(filepath):
                return False
            os.remove(filepath)
            return True

        deleted = await hass.async_add_executor_job(check_and_delete)
        if not deleted:
            raise HomeAssistantError(f"Backup file not found: {filepath}")
        logger.info(f"Deleted backup file: {filename}")

        # Refresh select entity
        if select_key in hass.data[DOMAIN]:
            await hass.data[DOMAIN][select_key].async_refresh_options()

        await hass.services.async_call("persistent_notification", "create", {
            "message": f"Backup file deleted: **{filename}**",
            "title": "NBE Delete Backup",
            "notification_id": "nbe_delete"
        })

    hass.services.async_register(DOMAIN, "delete_backup", handle_delete_backup)



    logger.info("NBELocalConnect setup complete!")
    return True


async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor", "button", "number", "select"])

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id + '_coordinator')
        hass.data[DOMAIN].pop(entry.entry_id + '_backup_select', None)
        hass.services.async_remove(DOMAIN, "set_setting")
        hass.services.async_remove(DOMAIN, "backup_settings")
        hass.services.async_remove(DOMAIN, "restore_settings")
        hass.services.async_remove(DOMAIN, "delete_backup")

    return unload_ok


class RTBDataCoordinator(DataUpdateCoordinator):
    """Data coordinator der henter ALT fra fyret."""

    def __init__(self, hass, entry_id, proxy, scan_interval, proxy_lock):
        """Initialize coordinator."""
        self.hass = hass
        self.entry_id = entry_id
        self.proxy = proxy
        self.rtbdata = RTBData([])
        self.info_message = 0
        self.info_messages = []
        self.translations = load_translations(hass.config.language)
        self.proxy_lock = proxy_lock
        self.last_raw_data = []  # Bruges af backup service

        update_interval = datetime.timedelta(seconds=scan_interval)
        super().__init__(hass, logger, name=DOMAIN, update_interval=update_interval)

    async def _async_update_data(self):
        """Fetch ALLE data fra fyret."""

        # locked_get holder proxy_lock mens get() kører i executor thread
        def locked_get(path):
            with self.proxy_lock:
                return self.proxy.get(path)

        try:
            logger.info("Fetching ALL data from boiler...")
            all_data = []

            # ================================================================
            # 1. OPERATING DATA
            # ================================================================
            logger.debug("Fetching operating_data/...")
            try:
                data = await self.hass.async_add_executor_job(locked_get, 'operating_data/')
                if data:
                    all_data.extend(data)
                    logger.debug(f"  ✓ Got {len(data)} operating_data items")
            except Exception as e:
                logger.debug(f"  ✗ Error fetching operating_data: {e}")

            # ================================================================
            # 2. ADVANCED DATA
            # ================================================================
            logger.debug("Fetching advanced_data/...")
            try:
                data = await self.hass.async_add_executor_job(locked_get, 'advanced_data/')
                if data:
                    all_data.extend(data)
                    logger.debug(f"  ✓ Got {len(data)} advanced_data items")
            except Exception as e:
                logger.debug(f"  ✗ Error fetching advanced_data: {e}")

            # ================================================================
            # 3. CONSUMPTION DATA
            # ================================================================
            logger.debug("Fetching consumption_data individually...")
            for key in [
                'consumption_data/counter',
                'consumption_data/total_hours',
                'consumption_data/total_days',
                'consumption_data/total_months',
                'consumption_data/total_years',
                'consumption_data/dhw_hours',
                'consumption_data/dhw_days',
                'consumption_data/dhw_months',
                'consumption_data/dhw_years',
            ]:
                try:
                    data = await self.hass.async_add_executor_job(locked_get, key)
                    if data:
                        all_data.extend(data)
                        logger.debug(f"  ✓ Got {key}")
                except Exception as e:
                    logger.debug(f"  ✗ No data for {key}")

            # ================================================================
            # 4. SETTINGS ENDPOINTS
            # ================================================================
            for endpoint in [
                'settings/boiler/',
                'settings/hot_water/',
                'settings/regulation/',
                'settings/weather/',
                'settings/weather2/',
                'settings/oxygen/',
                'settings/cleaning/',
                'settings/hopper/',
                'settings/fan/',
                'settings/auger/',
                'settings/ignition/',
                'settings/pump/',
                'settings/sun/',
                'settings/misc/',
                'settings/alarm/',
                'settings/manual/',
            ]:
                logger.debug(f"Fetching {endpoint}...")
                try:
                    data = await self.hass.async_add_executor_job(locked_get, endpoint)
                    if data:
                        all_data.extend(data)
                        logger.debug(f"  ✓ Got {len(data)} items from {endpoint}")
                except Exception as e:
                    logger.debug(f"  ✗ Error fetching {endpoint}: {e}")

            self.rtbdata.set(all_data)
            self.last_raw_data = all_data  # Store for backup service
            logger.info(f"✅ Successfully fetched {len(all_data)} total data points!")

            # ================================================================
            # 5. INFO MESSAGE
            # ================================================================
            logger.debug("Fetching info/...")
            try:
                data = await self.hass.async_add_executor_job(locked_get, 'info/')
                if data:
                    raw = data[0].strip() if data else '0'
                    parts = [p.strip() for p in raw.split(',') if p.strip()]
                    nums = []
                    for p in parts:
                        try:
                            n = int(p)
                            if n != 0:
                                nums.append(n)
                        except (ValueError, TypeError):
                            pass
                    self.info_messages = nums
                    self.info_message = nums[0] if nums else 0
                    logger.debug(f"  ✓ Info messages: {self.info_messages}")
            except Exception as e:
                logger.debug(f"  ✗ Error fetching info: {e}")
                self.info_messages = []
                self.info_message = 0

            return all_data

        except TimeoutError:
            logger.debug("Timeout fetching data. Will retry next interval.")
            return None
        except Exception as e:
            logger.error(f"Error fetching data: {e}")
            return None
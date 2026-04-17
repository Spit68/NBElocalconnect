import asyncio
import datetime
import threading
import json
import os
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, EVENT_CORE_CONFIG_UPDATE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from .const import DOMAIN
from .rtbdata import RTBData
from .protocol import Proxy
from logging import getLogger

logger = getLogger(__name__)


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

    # Hent konfiguration
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
    
    # Opret device
    device_registry = dr.async_get(hass)
    
    # Brug serial hvis tilgængelig, ellers IP
    device_identifier = proxy.serial if hasattr(proxy, 'serial') and proxy.serial else ip_address
    device_name = f"NBE Boiler {device_identifier}"
    
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=device_name,
        manufacturer="NBE",
        model="Pellet Boiler",
    )    
    
    # Opret coordinator
    proxy_lock = threading.Lock()
    coordinator = RTBDataCoordinator(
        hass, 
        entry.entry_id, 
        proxy,
        scan_interval,
        proxy_lock
    )
    
    # Hent initial data
    await coordinator.async_config_entry_first_refresh()
    
    # Gem coordinator
    hass.data[DOMAIN][entry.entry_id+'_coordinator'] = coordinator
    
    # Setup platforms
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "button", "number"])

    # Lyt efter sprog ændringer
    async def handle_language_change(event):
        """Reload translations when HA language changes."""
        new_language = hass.config.language
        logger.info(f"Language changed to '{new_language}', reloading translations...")
        coordinator.translations = load_translations(new_language)
        coordinator.async_update_listeners()

    entry.async_on_unload(
        hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, handle_language_change)
    )
    
    # Register service for setting values
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
                
                # CHECK OM WRITABLE!
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
        
        try:
            # Konverter value til string 
            result = await hass.async_add_executor_job(
                proxy.set, key, str(value)
            )
            logger.info(f"Successfully set {key} = {value}")
        except Exception as e:
            logger.error(f"Error setting {key}: {e}")
            raise
    
    hass.services.async_register(DOMAIN, "set_setting", handle_set_setting)
    
    logger.info("NBELocalConnect setup complete!")
    return True

async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor", "button", "number"])
    
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id+'_coordinator')
        # Unregister service
        hass.services.async_remove(DOMAIN, "set_setting")
    
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
        
        update_interval = datetime.timedelta(seconds=scan_interval)
        super().__init__(hass, logger, name=DOMAIN, update_interval=update_interval)
    
    async def _async_update_data(self):
        """Fetch ALLE data fra fyret."""
        proxy_lock = self.proxy_lock

        def locked_get(path):
            with proxy_lock:
                return self.proxy.get(path)

        try:
            logger.info("Fetching ALL data from boiler...")
            all_data = []

            logger.debug("Fetching operating_data/...")
            try:
                data = await self.hass.async_add_executor_job(locked_get, 'operating_data/')
                if data:
                    all_data.extend(data)
                    logger.debug(f"  ✓ Got {len(data)} operating_data items")
            except Exception as e:
                logger.debug(f"  ✗ Error fetching operating_data: {e}")

            logger.debug("Fetching advanced_data/...")
            try:
                data = await self.hass.async_add_executor_job(locked_get, 'advanced_data/')
                if data:
                    all_data.extend(data)
                    logger.debug(f"  ✓ Got {len(data)} advanced_data items")
            except Exception as e:
                logger.debug(f"  ✗ Error fetching advanced_data: {e}")

            logger.debug("Fetching consumption_data individually...")
            for key in ['consumption_data/counter','consumption_data/total_hours','consumption_data/total_days',
                        'consumption_data/total_months','consumption_data/total_years','consumption_data/dhw_hours',
                        'consumption_data/dhw_days','consumption_data/dhw_months','consumption_data/dhw_years']:
                try:
                    data = await self.hass.async_add_executor_job(locked_get, key)
                    if data:
                        all_data.extend(data)
                        logger.debug(f"  ✓ Got {key}")
                except Exception as e:
                    logger.debug(f"  ✗ No data for {key}")

            for endpoint in ['settings/boiler/','settings/hot_water/','settings/regulation/','settings/weather/',
                             'settings/weather2/','settings/oxygen/','settings/hopper/','settings/fan/',
                             'settings/auger/','settings/ignition/','settings/pump/','settings/sun/',
                             'settings/misc/','settings/alarm/','settings/manual/']:
                logger.debug(f"Fetching {endpoint}...")
                try:
                    data = await self.hass.async_add_executor_job(locked_get, endpoint)
                    if data:
                        all_data.extend(data)
                        logger.debug(f"  ✓ Got {len(data)} items from {endpoint}")
                except Exception as e:
                    logger.debug(f"  ✗ Error fetching {endpoint}: {e}")

            self.rtbdata.set(all_data)
            logger.info(f"✅ Successfully fetched {len(all_data)} total data points!")

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

async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor", "button", "number"])
    
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id+'_coordinator')
        # Unregister service
        hass.services.async_remove(DOMAIN, "set_setting")
    
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
        
        update_interval = datetime.timedelta(seconds=scan_interval)
        super().__init__(hass, logger, name=DOMAIN, update_interval=update_interval)
    
    async def _async_update_data(self):
        """Fetch ALLE data fra fyret."""
        async with self.proxy_lock:
            try:
                logger.info("Fetching ALL data from boiler...")
                all_data = []

                logger.debug("Fetching operating_data/...")
                try:
                    data = await self.hass.async_add_executor_job(self.proxy.get, 'operating_data/')
                    if data:
                        all_data.extend(data)
                        logger.debug(f"  ✓ Got {len(data)} operating_data items")
                except Exception as e:
                    logger.debug(f"  ✗ Error fetching operating_data: {e}")

                logger.debug("Fetching advanced_data/...")
                try:
                    data = await self.hass.async_add_executor_job(self.proxy.get, 'advanced_data/')
                    if data:
                        all_data.extend(data)
                        logger.debug(f"  ✓ Got {len(data)} advanced_data items")
                except Exception as e:
                    logger.debug(f"  ✗ Error fetching advanced_data: {e}")

                logger.debug("Fetching consumption_data individually...")
                for key in ['consumption_data/counter','consumption_data/total_hours','consumption_data/total_days',
                            'consumption_data/total_months','consumption_data/total_years','consumption_data/dhw_hours',
                            'consumption_data/dhw_days','consumption_data/dhw_months','consumption_data/dhw_years']:
                    try:
                        data = await self.hass.async_add_executor_job(self.proxy.get, key)
                        if data:
                            all_data.extend(data)
                            logger.debug(f"  ✓ Got {key}")
                    except Exception as e:
                        logger.debug(f"  ✗ No data for {key}")

                for endpoint in ['settings/boiler/','settings/hot_water/','settings/regulation/','settings/weather/',
                                 'settings/weather2/','settings/oxygen/','settings/hopper/','settings/fan/',
                                 'settings/auger/','settings/ignition/','settings/pump/','settings/sun/',
                                 'settings/misc/','settings/alarm/','settings/manual/']:
                    logger.debug(f"Fetching {endpoint}...")
                    try:
                        data = await self.hass.async_add_executor_job(self.proxy.get, endpoint)
                        if data:
                            all_data.extend(data)
                            logger.debug(f"  ✓ Got {len(data)} items from {endpoint}")
                    except Exception as e:
                        logger.debug(f"  ✗ Error fetching {endpoint}: {e}")

                self.rtbdata.set(all_data)
                logger.info(f"✅ Successfully fetched {len(all_data)} total data points!")

                logger.debug("Fetching info/...")
                try:
                    data = await self.hass.async_add_executor_job(self.proxy.get, 'info/')
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

async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor", "button", "number"])
    
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id+'_coordinator')
        # Unregister service
        hass.services.async_remove(DOMAIN, "set_setting")
    
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
        
        update_interval = datetime.timedelta(seconds=scan_interval)
        super().__init__(hass, logger, name=DOMAIN, update_interval=update_interval)
    
    async def _async_update_data(self):
        """Fetch ALLE data fra fyret."""
        try:
            logger.info("Fetching ALL data from boiler...")
            
            all_data = []
            
            # ================================================================
            # 1. OPERATING DATA
            # ================================================================
            logger.debug("Fetching operating_data/...")
            try:
                data = await self.hass.async_add_executor_job(
                    self.proxy.get, 'operating_data/'
                )
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
                data = await self.hass.async_add_executor_job(
                    self.proxy.get, 'advanced_data/'
                )
                if data:
                    all_data.extend(data)
                    logger.debug(f"  ✓ Got {len(data)} advanced_data items")
            except Exception as e:
                logger.debug(f"  ✗ Error fetching advanced_data: {e}")
            
            # ================================================================
            # 3. CONSUMPTION DATA (INDIVIDUELT!)
            # ================================================================
            logger.debug("Fetching consumption_data individually...")
            consumption_keys = [
                'consumption_data/counter',
                'consumption_data/total_hours',
                'consumption_data/total_days',
                'consumption_data/total_months',
                'consumption_data/total_years',
                'consumption_data/dhw_hours',
                'consumption_data/dhw_days',
                'consumption_data/dhw_months',
                'consumption_data/dhw_years',
            ]
            
            for key in consumption_keys:
                try:
                    data = await self.hass.async_add_executor_job(
                        self.proxy.get, key
                    )
                    if data:
                        all_data.extend(data)
                        logger.debug(f"  ✓ Got {key}")
                except Exception as e:
                    logger.debug(f"  ✗ No data for {key}")
            
            # ================================================================
            # 4. SETTINGS ENDPOINTS
            # ================================================================
            settings_endpoints = [
                'settings/boiler/',
                'settings/hot_water/',
                'settings/regulation/',
                'settings/weather/',
                'settings/weather2/',
                'settings/oxygen/',
                'settings/hopper/',
                'settings/fan/',
                'settings/auger/',
                'settings/ignition/',
                'settings/pump/',
                'settings/sun/',
                'settings/misc/',
                'settings/alarm/',
                'settings/manual/',
            ]
            
            for endpoint in settings_endpoints:
                logger.debug(f"Fetching {endpoint}...")
                try:
                    data = await self.hass.async_add_executor_job(
                        self.proxy.get, endpoint
                    )
                    if data:
                        all_data.extend(data)
                        logger.debug(f"  ✓ Got {len(data)} items from {endpoint}")
                except Exception as e:
                    logger.debug(f"  ✗ Error fetching {endpoint}: {e}")
            
            # Gem alt data
            self.rtbdata.set(all_data)
            logger.info(f"✅ Successfully fetched {len(all_data)} total data points!")

            # ================================================================
            # 5. INFO MESSAGE
            # ================================================================
            logger.debug("Fetching info/...")
            try:
                data = await self.hass.async_add_executor_job(
                    self.proxy.get, 'info/'
                )
                if data:
                    # Returnerer liste som ['13'] eller ['13,5'] ved flere beskeder
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
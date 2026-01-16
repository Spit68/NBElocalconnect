import datetime
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.exceptions import HomeAssistantError
from .const import DOMAIN
from .rtbdata import RTBData
from .protocol import Proxy
from logging import getLogger

logger = getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the NBE component."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass, entry):
    """Set up NBE from a config entry."""
    logger.info("Setting up NBELocalConnect integration...")
    
    # Hent konfiguration
    ip_address = entry.data.get('ip_address')
    password = entry.data.get(CONF_PASSWORD)
    port = entry.data.get('port', 8483)
    serialnumber = entry.data.get('serial', None)
    scan_interval = entry.data.get(CONF_SCAN_INTERVAL, 60)
    
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
    
    # Opret coordinator
    coordinator = RTBDataCoordinator(
        hass, 
        entry.entry_id, 
        proxy,
        scan_interval
    )
    
    # Hent initial data
    await coordinator.async_config_entry_first_refresh()
    
    # Gem coordinator
    hass.data[DOMAIN][entry.entry_id+'_coordinator'] = coordinator
    
    # Setup platforms
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "button"])
    
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
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor", "button"])
    
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id+'_coordinator')
        # Unregister service
        hass.services.async_remove(DOMAIN, "set_setting")
    
    return unload_ok


class RTBDataCoordinator(DataUpdateCoordinator):
    """Data coordinator der henter ALT fra fyret."""
    
    def __init__(self, hass, entry_id, proxy, scan_interval):
        """Initialize coordinator."""
        self.hass = hass
        self.entry_id = entry_id
        self.proxy = proxy
        self.rtbdata = RTBData([])
        
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
            
            return all_data
        
        except TimeoutError:
            logger.debug("Timeout fetching data. Will retry next interval.")
            return None
        except Exception as e:
            logger.error(f"Error fetching data: {e}")
            return None
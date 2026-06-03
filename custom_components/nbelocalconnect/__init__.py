import asyncio
import re
import datetime
import threading
import json
import os
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, EVENT_CORE_CONFIG_UPDATE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.exceptions import HomeAssistantError, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from datetime import timezone as dt_timezone
from .const import DOMAIN, NBE_BACKUP_DIR

STOKERCLOUD_BASE = "https://www.stokercloud.dk"
STOKERCLOUD_LOGIN = STOKERCLOUD_BASE + "/v2/dataout2/login.php"
STOKERCLOUD_CONSUMPTION = STOKERCLOUD_BASE + "/v2/dataout2/getconsumption.php"
# Boiler states that indicate an alarm condition
ALARM_STATES = {8, 11, 12, 13, 15, 16, 17, 19, 20, 26, 27, 29, 30, 31, 36, 37, 38, 39, 41, 42, 44, 45}
ALARM_HISTORY_MAX = 25

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


async def async_load_translations(hass, language: str) -> dict:
    """Load boiler message translations for given language, fallback to en.

    File I/O is run in executor to avoid blocking Home Assistant's event loop.
    """
    language = (language or "en").replace("_", "-").split("-")[0].lower()

    translations_dir = os.path.join(os.path.dirname(__file__), "boiler_messages")
    lang_file = os.path.join(translations_dir, f"{language}.json")

    if not os.path.exists(lang_file):
        logger.debug("No boiler message file for '%s', falling back to en", language)
        lang_file = os.path.join(translations_dir, "en.json")

    def _load_file():
        with open(lang_file, encoding="utf-8") as f:
            return json.load(f)

    try:
        return await hass.async_add_executor_job(_load_file)
    except Exception as e:
        logger.error("Error loading boiler messages from %s: %s", lang_file, e)
        return {"boiler_state": {}, "boiler_substate": {}, "boiler_info": {}}


async def async_fetch_stokercloud(hass, username: str) -> dict:
    """Login til StokerCloud og hent 12 aars forbrugsdata.
    Returnerer {'pellets': [...], 'dhw': [...]} eller None ved fejl.
    Data er sorteret nyeste aar foerst - samme format som andre consumption sensorer.
    Hvert element i raw data er [timestamp_ms, kg_value]."""
    session = async_get_clientsession(hass)
    try:
        # Login
        async with session.get(STOKERCLOUD_LOGIN, params={"user": username}, timeout=15) as resp:
            if resp.status != 200:
                logger.error(f"StokerCloud login error: HTTP {resp.status}")
                return None
            login_data = await resp.json(content_type=None)

        token = login_data.get("token") or login_data.get("result")
        if not token or str(token).lower() in ("", "error", "false", "null"):
            logger.error(f"StokerCloud login failed: {login_data}")
            return None

        logger.info("StokerCloud login OK, fetching consumption...")

        # Hent forbrug
        async with session.get(STOKERCLOUD_CONSUMPTION, params={"token": token, "years": 12}, timeout=15) as resp:
            if resp.status != 200:
                logger.error(f"StokerCloud consumption error: HTTP {resp.status}")
                return None
            raw = await resp.json(content_type=None)

        # Format: array af objekter med label og data=[[timestamp_ms, kg], ...]
        # label "graph_consume" = pellets, "graph_consume setup_vvb" = DHW
        pellets = []
        dhw = []
        timestamps = []  # Unix ms, nyeste foerst
        for item in (raw if isinstance(raw, list) else []):
            label = item.get("label", "")
            entries = item.get("data", [])
            values = [round(float(e[1]), 3) for e in entries if e[1] is not None]
            if label == "graph_consume":
                pellets = values
                timestamps = [int(e[0]) for e in entries if e[1] is not None]
            elif "setup_vvb" in label:
                dhw = values

        logger.info(f"StokerCloud: {len(pellets)} pellet years, {len(dhw)} DHW years hentet")
        return {"pellets": pellets, "dhw": dhw, "timestamps": timestamps}

    except Exception as e:
        logger.error(f"StokerCloud fetch error: {e}", exc_info=True)
        return None


def _is_dhw_entity_enabled(hass, entry_id: str) -> bool:
    """Tjek om DHW yearly sensor er aktiveret i entity registry."""
    from homeassistant.helpers import entity_registry as er
    ent_reg = er.async_get(hass)
    uid = f"{entry_id}_v2_dhw_yearly"
    entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, uid)
    if not entity_id:
        return False
    entry = ent_reg.async_get(entity_id)
    return entry is not None and entry.disabled_by is None




async def _get_year_state_from_db(hass, statistic_id: str) -> float:
    """Laes indevaerende aars state-vaerdi fra HA statistics DB. Returnerer 0.0 hvis ingen data eller forkert år."""
    recorder_instance = get_instance(hass)

    def _do_get():
        return get_last_statistics(hass, 1, statistic_id, True, {"state"})

    try:
        result = await recorder_instance.async_add_executor_job(_do_get)
        if result and statistic_id in result:
            entries = result[statistic_id]
            if entries:
                entry = entries[0]
                # Tjek at entry er fra indeværende år i HA's lokale tidszone
                entry_start = entry.get("start")
                if entry_start is not None:
                    from zoneinfo import ZoneInfo
                    tz = ZoneInfo(hass.config.time_zone)
                    current_year = datetime.datetime.now(tz=tz).year
                    if hasattr(entry_start, "year"):
                        if entry_start.tzinfo is None:
                            entry_start = entry_start.replace(tzinfo=dt_timezone.utc)
                        entry_local = entry_start.astimezone(tz)
                    else:
                        entry_local = datetime.datetime.fromtimestamp(entry_start, tz=tz)
                    if entry_local.year != current_year:
                        return 0.0
                return float(entry.get("state") or 0.0)
    except Exception as e:
        logger.debug(f"Could not read statistics for {statistic_id}: {e}")
    return 0.0




def _current_year_ts_ms(hass) -> int:
    """Returnerer Unix timestamp i ms for 1. jan indeværende år i HA's konfigurerede tidszone."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(hass.config.time_zone)
    now = datetime.datetime.now(tz=tz)
    year_start = datetime.datetime(now.year, 1, 1, tzinfo=tz)
    return int(year_start.timestamp() * 1000)


def _today_ts_ms(hass) -> int:
    """Returnerer Unix timestamp i ms for midnat i dag i HA's konfigurerede tidszone."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(hass.config.time_zone)
    now = datetime.datetime.now(tz=tz)
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(today_midnight.timestamp() * 1000)


async def _get_today_state_from_db(hass, statistic_id: str) -> float:
    """Laes dagens state-vaerdi fra HA statistics DB. Returnerer 0.0 hvis ingen data eller forkert dag."""
    recorder_instance = get_instance(hass)

    def _do_get():
        return get_last_statistics(hass, 1, statistic_id, True, {"state"})

    try:
        result = await recorder_instance.async_add_executor_job(_do_get)
        if result and statistic_id in result:
            entries = result[statistic_id]
            if entries:
                entry = entries[0]
                entry_start = entry.get("start")
                if entry_start is not None:
                    from zoneinfo import ZoneInfo
                    tz = ZoneInfo(hass.config.time_zone)
                    today = datetime.datetime.now(tz=tz).date()
                    if hasattr(entry_start, "year"):
                        if entry_start.tzinfo is None:
                            entry_start = entry_start.replace(tzinfo=dt_timezone.utc)
                        entry_local = entry_start.astimezone(tz)
                    else:
                        entry_local = datetime.datetime.fromtimestamp(entry_start, tz=tz)
                    if entry_local.date() != today:
                        return 0.0
                return float(entry.get("state") or 0.0)
    except Exception as e:
        logger.debug(f"Could not read daily statistics for {statistic_id}: {e}")
    return 0.0


async def async_inject_daily_statistics(hass, entry_id: str, stat_suffix: str, timestamp_ms: int, value: float):
    """Injicer/opdater en enkelt dags forbrugsdata i HA statistics DB."""
    statistic_id = _yearly_statistic_id(entry_id, stat_suffix)
    metadata = StatisticMetaData(
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_of_measurement="kg",
        unit_class=None,
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=f"NBE {stat_suffix.replace('_', ' ').title()}",
    )
    start_dt = datetime.datetime.fromtimestamp(timestamp_ms / 1000, tz=dt_timezone.utc)
    stats = [StatisticData(start=start_dt, state=value, sum=value)]
    async_add_external_statistics(hass, metadata, stats)


def _sort_daily_for_import(raw_data_str: str, current_day: int, last_month_days: int) -> list:
    """Sorterer boilerens daglige array til korrekt rækkefølge (nyeste først).
    Filtrerer stale data fra måneder med færre end 31 dage."""
    try:
        values_str = raw_data_str.split('=')[1] if '=' in raw_data_str else raw_data_str
        values = [float(v.strip()) for v in values_str.split(',') if v.strip()]
        this_month = list(reversed(values[:current_day]))
        last_month = list(reversed(values[current_day:last_month_days]))
        return this_month + last_month
    except (ValueError, IndexError):
        return []


async def async_import_daily_from_boiler(hass, coordinator):
    """Importer boilerens daglige data til HA statistics DB og koordinator hukommelse.
    Køres kun ved setup hvis ingen daglig data findes i DB.
    Filtrerer stale data og bruger korrekte dag-timestamps."""
    import calendar
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(hass.config.time_zone)
    today = datetime.datetime.now(tz=tz)
    current_day = today.day
    today_midnight = today.replace(hour=0, minute=0, second=0, microsecond=0)

    if today.month == 1:
        last_month_days = calendar.monthrange(today.year - 1, 12)[1]
    else:
        last_month_days = calendar.monthrange(today.year, today.month - 1)[1]

    def _build_stats(sorted_values, stat_suffix):
        statistic_id = _yearly_statistic_id(coordinator.statistic_identifier, stat_suffix)
        metadata = StatisticMetaData(
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_of_measurement="kg",
            unit_class=None,
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"NBE {stat_suffix.replace('_', ' ').title()}",
        )
        stats = []
        for i, value in enumerate(sorted_values):
            day_midnight = today_midnight - datetime.timedelta(days=i)
            start_dt = day_midnight.astimezone(dt_timezone.utc)
            stats.append(StatisticData(start=start_dt, state=value, sum=value))
        async_add_external_statistics(hass, metadata, stats)

    def _build_timestamps(n):
        return [int((today_midnight - datetime.timedelta(days=i)).timestamp() * 1000) for i in range(n)]

    # Pellets
    raw_pellets = coordinator.rtbdata.get("consumption_data/total_days")
    if raw_pellets:
        sorted_pellets = _sort_daily_for_import(raw_pellets, current_day, last_month_days)
        if sorted_pellets:
            _build_stats(sorted_pellets, "pellets_daily")
            coordinator.stokercloud_daily_pellets = sorted_pellets
            coordinator.stokercloud_daily_timestamps = _build_timestamps(len(sorted_pellets))
            logger.info(f"Daglig pellets import: {len(sorted_pellets)} dage importeret")

    # DHW
    raw_dhw = coordinator.rtbdata.get("consumption_data/dhw_days")
    if raw_dhw:
        sorted_dhw = _sort_daily_for_import(raw_dhw, current_day, last_month_days)
        if sorted_dhw:
            _build_stats(sorted_dhw, "dhw_daily")
            coordinator.stokercloud_daily_dhw = sorted_dhw
            logger.info(f"Daglig DHW import: {len(sorted_dhw)} dage importeret")

    coordinator._last_known_day = current_day


async def async_load_daily_from_db(hass, coordinator):
    """Loader de seneste 31 dages daglige data fra HA statistics DB ind i koordinator hukommelse."""
    stat_identifier = coordinator.statistic_identifier
    stat_id = _yearly_statistic_id(stat_identifier, "pellets_daily")
    stat_id_dhw = _yearly_statistic_id(stat_identifier, "dhw_daily")
    recorder_instance = get_instance(hass)

    def _do_get_pellets():
        return get_last_statistics(hass, 31, stat_id, True, {"state"})

    def _do_get_dhw():
        return get_last_statistics(hass, 31, stat_id_dhw, True, {"state"})

    # Pellets
    try:
        result = await recorder_instance.async_add_executor_job(_do_get_pellets)
        if result and stat_id in result and result[stat_id]:
            entries = result[stat_id]
            pellets_values = []
            pellets_timestamps = []
            for entry in entries:
                pellets_values.append(float(entry.get("state") or 0.0))
                entry_start = entry.get("start")
                if entry_start is not None:
                    if hasattr(entry_start, "timestamp"):
                        if entry_start.tzinfo is None:
                            entry_start = entry_start.replace(tzinfo=dt_timezone.utc)
                        ts_ms = int(entry_start.timestamp() * 1000)
                    else:
                        ts_ms = int(float(entry_start) * 1000)
                    pellets_timestamps.append(ts_ms)
            coordinator.stokercloud_daily_pellets = pellets_values
            coordinator.stokercloud_daily_timestamps = pellets_timestamps
            logger.info(f"Daglig pellets loadet fra DB: {len(pellets_values)} dage")
    except Exception as e:
        logger.debug(f"Kunne ikke loade daglig pellets fra DB: {e}")
        coordinator.stokercloud_daily_pellets = []
        coordinator.stokercloud_daily_timestamps = []

    # DHW
    try:
        result = await recorder_instance.async_add_executor_job(_do_get_dhw)
        if result and stat_id_dhw in result and result[stat_id_dhw]:
            entries = result[stat_id_dhw]
            coordinator.stokercloud_daily_dhw = [float(e.get("state") or 0.0) for e in entries]
            logger.info(f"Daglig DHW loadet fra DB: {len(coordinator.stokercloud_daily_dhw)} dage")
    except Exception as e:
        logger.debug(f"Kunne ikke loade daglig DHW fra DB: {e}")
        coordinator.stokercloud_daily_dhw = []

    # Sæt last known day
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(hass.config.time_zone)
    coordinator._last_known_day = datetime.datetime.now(tz=tz).day


def _clean_statistic_part(value: str) -> str:
    """Make statistic_id part Home Assistant safe."""
    value = value.lower().replace("-", "_")
    value = re.sub(r"[^a-z0-9_]", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def _yearly_statistic_id(identifier: str, stat_suffix: str) -> str:
    """Build valid Home Assistant statistic_id.

    The identifier should be the boiler serial number when available.
    This keeps statistics stable if the integration is deleted/re-added,
    and makes Lovelace examples easier to use than config-entry ULIDs.
    """
    safe_domain = _clean_statistic_part(DOMAIN)
    safe_suffix = _clean_statistic_part(stat_suffix)
    safe_identifier = _clean_statistic_part(str(identifier))
    return f"{safe_domain}:{safe_suffix}_{safe_identifier}"

async def async_inject_yearly_statistics(hass, entry_id: str, stat_suffix: str,
                                          timestamps_ms: list, values: list):
    """Injicer/opdater aarlige forbrugsdata i HA statistics DB.
    timestamps_ms og values er sorteret nyeste foerst.
    Kalder async_add_external_statistics som er upsert."""
    if not timestamps_ms or not values:
        return

    statistic_id = _yearly_statistic_id(entry_id, stat_suffix)
    metadata = StatisticMetaData(
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_of_measurement="kg",
        unit_class=None,
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=f"NBE {stat_suffix.replace('_', ' ').title()}",
    )

    # Sorter aeldste foerst til kumulativ sum
    pairs = list(reversed(list(zip(timestamps_ms, values))))
    stats = []
    cumsum = 0.0
    for ts_ms, val in pairs:
        cumsum += val
        start_dt = datetime.datetime.fromtimestamp(ts_ms / 1000, tz=dt_timezone.utc)
        stats.append(StatisticData(start=start_dt, state=val, sum=round(cumsum, 3)))

    async_add_external_statistics(hass, metadata, stats)


def _read_daily_from_rtbdata(rtbdata, key: str) -> float:
    """Læs indeværende dags værdi fra et månedligt array i rtbdata.
    Returnerer 0.0 hvis data ikke er tilgængelig."""
    raw = rtbdata.get(key)
    if not raw:
        return 0.0
    try:
        parts = [float(v.strip()) for v in str(raw).split("=")[-1].split(",") if v.strip()]
        current_d = datetime.datetime.now().day - 1
        return parts[current_d] if len(parts) > current_d else 0.0
    except (ValueError, IndexError):
        return 0.0


async def async_load_yearly_from_db(hass, coordinator):
    """Initialiser 12 aars yearly consumption i coordinator memory.
    Historiske år (1-11) loades fra Store hvis tilgængeligt (fra StokerCloud import).
    Nuværende år (0) læses altid fra HA DB for at få seneste akkumulerede værdi.
    Timestamps er 1. jan for hvert år, nyeste først, i HA's lokale tidszone.
    DHW initialiseres altid - uanset om entity er aktiveret.
    """
    stat_identifier = coordinator.statistic_identifier
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(hass.config.time_zone)
    now_local = datetime.datetime.now(tz=tz)
    current_year = now_local.year

    # Byg 12 års timestamps (nyeste først) i HA's lokale tidszone
    timestamps = []
    for i in range(12):
        year_start = datetime.datetime(current_year - i, 1, 1, tzinfo=tz)
        timestamps.append(int(year_start.timestamp() * 1000))

    # Prøv at loade historiske år fra Store (gemt ved StokerCloud import)
    history_data = await coordinator._history_store.async_load()

    # Pellets
    stat_id = _yearly_statistic_id(stat_identifier, "pellets_yearly")
    pellets_sum = await _get_year_state_from_db(hass, stat_id)

    if history_data and isinstance(history_data, dict) and len(history_data.get("pellets", [])) >= 12:
        # Brug historiske år fra Store, opdater kun indeværende år fra DB
        pellets_values = list(history_data["pellets"])
        pellets_values[0] = pellets_sum
        logger.info(f"Yearly pellets: historiske år loadet fra Store, indeværende år = {pellets_sum} kg")
    else:
        # Ingen historik i Store - start fra 0 for alle år
        pellets_values = [0.0] * 12
        pellets_values[0] = pellets_sum
        logger.info(f"Yearly pellets initialiseret: {pellets_sum} kg indeværende år, ingen historisk data")

    coordinator.stokercloud_pellets = pellets_values
    coordinator.stokercloud_timestamps = timestamps

    # DHW
    stat_id_dhw = _yearly_statistic_id(stat_identifier, "dhw_yearly")
    dhw_sum = await _get_year_state_from_db(hass, stat_id_dhw)

    if history_data and isinstance(history_data, dict) and len(history_data.get("dhw", [])) >= 12:
        dhw_values = list(history_data["dhw"])
        dhw_values[0] = dhw_sum
        logger.info(f"Yearly DHW: historiske år loadet fra Store, indeværende år = {dhw_sum} kg")
    else:
        dhw_values = [0.0] * 12
        dhw_values[0] = dhw_sum

    coordinator.stokercloud_dhw = dhw_values


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
        raise ConfigEntryNotReady(f"Cannot connect to boiler: {e}") from e

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
        proxy_lock,
        device_identifier
    )

    # Load boiler message translations before entities are created.
    coordinator.translations = await async_load_translations(hass, hass.config.language)

    # Fetch initial data
    await coordinator.async_load_alarm_history()
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator
    hass.data[DOMAIN][entry.entry_id + '_coordinator'] = coordinator

    # Setup platforms
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "button", "number", "select", "switch"])

    # StokerCloud import / yearly data initialisering
    stokercloud_enabled = entry.data.get("stokercloud_enabled", False)
    stokercloud_username = entry.data.get("stokercloud_username", "").strip()

    if stokercloud_enabled and stokercloud_username:
        # Sæt toggle til OFF med det samme uanset hvad der sker - i både data og options
        new_data = dict(entry.data)
        new_data["stokercloud_enabled"] = False
        new_options = dict(entry.options) if entry.options else {}
        if "stokercloud_enabled" in new_options:
            new_options["stokercloud_enabled"] = False
        hass.config_entries.async_update_entry(entry, data=new_data, options=new_options)
        logger.info("StokerCloud toggle reset to OFF")

        logger.info(f"StokerCloud import: fetching data for '{stokercloud_username}'...")
        result = await async_fetch_stokercloud(hass, stokercloud_username)
        if result:
            coordinator.stokercloud_pellets = result["pellets"]
            coordinator.stokercloud_timestamps = result["timestamps"]
            coordinator._helper2_pellets = None
            coordinator._helper2_dhw = None
            # Importer altid pellets til DB
            await async_inject_yearly_statistics(
                hass, coordinator.statistic_identifier, "pellets_yearly",
                result["timestamps"], result["pellets"]
            )
            # Importer altid DHW til DB - uanset om entity er aktiveret
            # Så er data klar når brugeren aktiverer DHW entity
            coordinator.stokercloud_dhw = result["dhw"]
            await async_inject_yearly_statistics(
                hass, coordinator.statistic_identifier, "dhw_yearly",
                result["timestamps"], result["dhw"]
            )
            logger.info(f"StokerCloud: {len(result['pellets'])} pellet years, {len(result['dhw'])} DHW years importeret")
            # Gem historiske år til Store så de overlever genstarter
            await coordinator._history_store.async_save({
                "pellets": result["pellets"],
                "dhw": result["dhw"],
                "timestamps": result["timestamps"]
            })
            # Sæt helper2 = helper1 så delta logik ikke tæller dagens forbrug dobbelt
            h1_pellets = _read_daily_from_rtbdata(coordinator.rtbdata, "consumption_data/total_days")
            h1_dhw = _read_daily_from_rtbdata(coordinator.rtbdata, "consumption_data/dhw_days")
            await coordinator._helper2_store.async_save({"helper2_pellets": h1_pellets, "helper2_dhw": h1_dhw})
            await coordinator.async_load_helper2()
            coordinator.async_update_listeners()
        else:
            logger.warning("StokerCloud import failed - loading from HA DB instead")
            await async_load_yearly_from_db(hass, coordinator)
            await coordinator.async_load_helper2()
    else:
        # Toggle OFF: læs indeværende års data fra HA DB (eller start fra 0 hvis ingen data)
        await async_load_yearly_from_db(hass, coordinator)
        await coordinator.async_load_helper2()

    # Daglig data: tjek om der er data i DB - importer fra fyret hvis ikke
    stat_id_daily_check = _yearly_statistic_id(coordinator.statistic_identifier, "pellets_daily")
    recorder_instance_check = get_instance(hass)

    def _check_daily_exists():
        result = get_last_statistics(hass, 1, stat_id_daily_check, True, {"state"})
        return bool(result and stat_id_daily_check in result and result[stat_id_daily_check])

    daily_exists = await recorder_instance_check.async_add_executor_job(_check_daily_exists)

    if not daily_exists:
        logger.info("Ingen daglig statistik fundet i DB - importerer fra fyret...")
        await async_import_daily_from_boiler(hass, coordinator)
    else:
        logger.info("Daglig statistik fundet i DB - loader...")
        await async_load_daily_from_db(hass, coordinator)

    # Opdater alle entities med det samme - ingen ventetid på næste poll
    coordinator.async_update_listeners()


    # Listen for language changes
    async def handle_language_change(event):
        """Reload translations when HA language changes."""
        new_language = hass.config.language
        logger.info(f"Language changed to '{new_language}', reloading translations...")
        coordinator.translations = await async_load_translations(hass, new_language)
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

    # ----------------------------------------------------------------
    # Service: import_stokercloud
    # Henter forbrugsdata fra StokerCloud og opdaterer coordinator
    # ----------------------------------------------------------------
    async def handle_import_stokercloud(call):
        """Hent og gem StokerCloud forbrugsdata."""
        username = entry.data.get("stokercloud_username", "").strip()
        if not username:
            await hass.services.async_call("persistent_notification", "create", {
                "message": "StokerCloud username not configured. Go to Configure on the integration.",
                "title": "NBE StokerCloud",
                "notification_id": "nbe_stokercloud_error"
            })
            return

        result = await async_fetch_stokercloud(hass, username)
        if result:
            coordinator.stokercloud_pellets = result["pellets"]
            coordinator.stokercloud_timestamps = result["timestamps"]
            coordinator._helper2_pellets = None
            await async_inject_yearly_statistics(
                hass, coordinator.statistic_identifier, "pellets_yearly",
                result["timestamps"], result["pellets"]
            )
            if _is_dhw_entity_enabled(hass, entry.entry_id):
                coordinator.stokercloud_dhw = result["dhw"]
                coordinator._helper2_dhw = None
                await async_inject_yearly_statistics(
                    hass, coordinator.statistic_identifier, "dhw_yearly",
                    result["timestamps"], result["dhw"]
                )
                dhw_msg = f", {len(result['dhw'])} DHW years"
            else:
                dhw_msg = " (DHW not enabled)"
            coordinator.async_update_listeners()
            h1_pellets = _read_daily_from_rtbdata(coordinator.rtbdata, "consumption_data/total_days")
            h1_dhw = _read_daily_from_rtbdata(coordinator.rtbdata, "consumption_data/dhw_days")
            await coordinator._helper2_store.async_save({"helper2_pellets": h1_pellets, "helper2_dhw": h1_dhw})
            await coordinator.async_load_helper2()
            await hass.services.async_call("persistent_notification", "create", {
                "message": f"StokerCloud import completed: {len(result['pellets'])} pellet years{dhw_msg}.",
                "title": "NBE StokerCloud",
                "notification_id": "nbe_stokercloud_ok"
            })
            logger.info("StokerCloud import completed via service")
        else:
            await hass.services.async_call("persistent_notification", "create", {
                "message": "StokerCloud import failed. Check username and internet connection.",
                "title": "NBE StokerCloud",
                "notification_id": "nbe_stokercloud_error"
            })

    hass.services.async_register(DOMAIN, "import_stokercloud", handle_import_stokercloud)

    hass.services.async_register(DOMAIN, "delete_backup", handle_delete_backup)



    logger.info("NBELocalConnect setup complete!")
    return True


async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor", "button", "number", "select", "switch"])

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id + '_coordinator')
        hass.data[DOMAIN].pop(entry.entry_id + '_backup_select', None)
        hass.data[DOMAIN].pop(entry.entry_id + '_energy_kwh', None)
        hass.data[DOMAIN].pop(entry.entry_id + '_energy_wh', None)
        hass.services.async_remove(DOMAIN, "set_setting")
        hass.services.async_remove(DOMAIN, "backup_settings")
        hass.services.async_remove(DOMAIN, "restore_settings")
        hass.services.async_remove(DOMAIN, "delete_backup")
        hass.services.async_remove(DOMAIN, "import_stokercloud")

    return unload_ok


class RTBDataCoordinator(DataUpdateCoordinator):
    """Data coordinator der henter ALT fra fyret."""

    def __init__(self, hass, entry_id, proxy, scan_interval, proxy_lock, statistic_identifier):
        """Initialize coordinator."""
        self.hass = hass
        self.entry_id = entry_id
        self.statistic_identifier = str(statistic_identifier or entry_id)
        self.proxy = proxy
        self.rtbdata = RTBData([])
        self.info_message = 0
        self.info_messages = []
        self.translations = {"boiler_state": {}, "boiler_substate": {}, "boiler_info": {}}
        self.proxy_lock = proxy_lock
        self.last_raw_data = []  # Bruges af backup service
        self.stokercloud_pellets = []    # Importeret fra StokerCloud
        self.stokercloud_dhw = []        # Importeret fra StokerCloud
        self.stokercloud_timestamps = [] # Unix ms, nyeste foerst (fra StokerCloud)
        self.stokercloud_daily_pellets = []    # Daglig pellets fra HA DB
        self.stokercloud_daily_dhw = []        # Daglig DHW fra HA DB
        self.stokercloud_daily_timestamps = [] # Unix ms, nyeste foerst (daglig)
        # To-helper delta tracking
        self._helper2_pellets = None     # Previous poll hourly value[0]
        self._helper2_dhw = None         # Previous poll dhw_hours[0]
        self._last_known_day = None      # Dag-skift detektion
        # Alarm history
        self._alarm_history = []
        self._last_alarm_state = None
        self._alarm_store = Store(hass, 1, f"{DOMAIN}_alarm_history_{entry_id}")
        self._helper2_store = Store(hass, 1, f"{DOMAIN}_helper2_{entry_id}")
        self._history_store = Store(hass, 1, f"{DOMAIN}_yearly_history_{entry_id}")

        update_interval = datetime.timedelta(seconds=scan_interval)
        super().__init__(hass, logger, name=DOMAIN, update_interval=update_interval)

    async def async_load_alarm_history(self):
        """Load alarm history from persistent storage."""
        data = await self._alarm_store.async_load()
        if data and isinstance(data, list):
            self._alarm_history = data[-ALARM_HISTORY_MAX:]
            logger.debug(f"Loaded {len(self._alarm_history)} alarm history entries")

    async def async_load_helper2(self):
        """Load helper2 values from persistent storage."""
        data = await self._helper2_store.async_load()
        if data and isinstance(data, dict):
            self._helper2_pellets = data.get("helper2_pellets")
            self._helper2_dhw = data.get("helper2_dhw")
            logger.debug(f"Loaded helper2: pellets={self._helper2_pellets}, dhw={self._helper2_dhw}")

    def get_translated_alarm_history(self) -> list:
        """Return alarm history with translated messages based on current language."""
        result = []
        state_translations = self.translations.get("boiler_state", {})
        for entry in reversed(self._alarm_history):
            code = entry.get("code")
            message = state_translations.get(str(code), f"State {code}")
            result.append({
                "code": code,
                "timestamp": entry.get("timestamp"),
                "message": message,
            })
        return result

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
                'consumption_data/dhw_hours',
                'consumption_data/dhw_days',
                'consumption_data/dhw_months',
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

            if all_data:
                self.rtbdata.set(all_data)
                self.last_raw_data = all_data  # Store for backup service
                logger.info(f"✅ Successfully fetched {len(all_data)} total data points!")
            else:
                logger.warning("Poll returned empty data - keeping last known values")

            # Alarm history tracking
            state_raw = self.rtbdata.get('operating_data/state')
            try:
                current_state = int(state_raw) if state_raw else None
            except (ValueError, TypeError):
                current_state = None

            if current_state in ALARM_STATES and current_state != self._last_alarm_state:
                entry = {
                    "code": current_state,
                    "timestamp": datetime.datetime.now().isoformat(timespec='seconds'),
                }
                self._alarm_history.append(entry)
                if len(self._alarm_history) > ALARM_HISTORY_MAX:
                    self._alarm_history = self._alarm_history[-ALARM_HISTORY_MAX:]
                await self._alarm_store.async_save(self._alarm_history)
                logger.info(f"Alarm logged: state {current_state}")

            self._last_alarm_state = current_state

            # ================================================================
            # DELTA LOGIC: Two-helper method with HA DB
            # helper1 = total_days[current_hour] (felt 1, altid frisk fra fyret)
            # if helper2 > helper1: new period, reset helper2
            # if helper1 > helper2: sum from DB + delta → skriv til DB → opdater entity
            # helper2 = helper1
            # ================================================================
            def _read_daily_field(key: str) -> float | None:
                raw = self.rtbdata.get(key)
                if not raw:
                    return None
                try:
                    parts = [float(v.strip()) for v in str(raw).split("=")[-1].split(",") if v.strip()]
                    current_d = datetime.datetime.now().day - 1
                    return parts[current_d] if len(parts) > current_d else None
                except (ValueError, IndexError):
                    return None

            if self.stokercloud_pellets and self.stokercloud_timestamps:
                # Opdater timestamps[0] til indeværende år - håndterer nytår automatisk
                self.stokercloud_timestamps[0] = _current_year_ts_ms(self.hass)
                stat_id = _yearly_statistic_id(self.statistic_identifier, "pellets_yearly")
                helper1 = _read_daily_field("consumption_data/total_days")
                if helper1 is not None:
                    # Dag-skift detektion
                    from zoneinfo import ZoneInfo as _ZI
                    _tz = _ZI(self.hass.config.time_zone)
                    _current_day = datetime.datetime.now(tz=_tz).day
                    if self._last_known_day is not None and _current_day != self._last_known_day:
                        logger.info(f"Dag-skift detekteret: {self._last_known_day} → {_current_day}")
                        _new_ts = _today_ts_ms(self.hass)
                        if self.stokercloud_daily_pellets:
                            self.stokercloud_daily_pellets = [0.0] + self.stokercloud_daily_pellets[:30]
                            self.stokercloud_daily_timestamps = [_new_ts] + self.stokercloud_daily_timestamps[:30]
                        if self.stokercloud_daily_dhw:
                            self.stokercloud_daily_dhw = [0.0] + self.stokercloud_daily_dhw[:30]
                    self._last_known_day = _current_day

                    if self._helper2_pellets is None:
                        self._helper2_pellets = 0.0
                    if self._helper2_pellets > helper1:
                        self._helper2_pellets = 0.0
                    if helper1 > self._helper2_pellets:
                        delta = helper1 - self._helper2_pellets
                        db_sum = await _get_year_state_from_db(self.hass, stat_id)
                        new_sum = round(db_sum + delta, 3)
                        self.stokercloud_pellets[0] = new_sum
                        await async_inject_yearly_statistics(
                            self.hass, self.statistic_identifier, "pellets_yearly",
                            [self.stokercloud_timestamps[0]], [new_sum]
                        )
                        # Opdater daglig pellets (samme delta)
                        if self.stokercloud_daily_pellets and self.stokercloud_daily_timestamps:
                            stat_id_daily = _yearly_statistic_id(self.statistic_identifier, "pellets_daily")
                            db_sum_daily = await _get_today_state_from_db(self.hass, stat_id_daily)
                            new_sum_daily = round(db_sum_daily + delta, 3)
                            self.stokercloud_daily_pellets[0] = new_sum_daily
                            await async_inject_daily_statistics(
                                self.hass, self.statistic_identifier, "pellets_daily",
                                self.stokercloud_daily_timestamps[0], new_sum_daily
                            )
                    self._helper2_pellets = helper1
                    await self._helper2_store.async_save({"helper2_pellets": self._helper2_pellets, "helper2_dhw": self._helper2_dhw})

            if _is_dhw_entity_enabled(self.hass, self.entry_id) and self.stokercloud_dhw and self.stokercloud_timestamps:
                # Opdater timestamps[0] til indeværende år - håndterer nytår automatisk
                self.stokercloud_timestamps[0] = _current_year_ts_ms(self.hass)
                stat_id_dhw = _yearly_statistic_id(self.statistic_identifier, "dhw_yearly")
                helper1_dhw = _read_daily_field("consumption_data/dhw_days")
                if helper1_dhw is not None:
                    if self._helper2_dhw is None:
                        self._helper2_dhw = 0.0
                    if self._helper2_dhw > helper1_dhw:
                        self._helper2_dhw = 0.0
                    if helper1_dhw > self._helper2_dhw:
                        delta_dhw = helper1_dhw - self._helper2_dhw
                        db_sum_dhw = await _get_year_state_from_db(self.hass, stat_id_dhw)
                        new_sum_dhw = round(db_sum_dhw + delta_dhw, 3)
                        self.stokercloud_dhw[0] = new_sum_dhw
                        await async_inject_yearly_statistics(
                            self.hass, self.statistic_identifier, "dhw_yearly",
                            [self.stokercloud_timestamps[0]], [new_sum_dhw]
                        )
                        # Opdater daglig DHW (samme delta)
                        if self.stokercloud_daily_dhw and self.stokercloud_daily_timestamps:
                            stat_id_dhw_daily = _yearly_statistic_id(self.statistic_identifier, "dhw_daily")
                            db_sum_dhw_daily = await _get_today_state_from_db(self.hass, stat_id_dhw_daily)
                            new_sum_dhw_daily = round(db_sum_dhw_daily + delta_dhw, 3)
                            self.stokercloud_daily_dhw[0] = new_sum_dhw_daily
                            await async_inject_daily_statistics(
                                self.hass, self.statistic_identifier, "dhw_daily",
                                self.stokercloud_daily_timestamps[0], new_sum_dhw_daily
                            )
                    self._helper2_dhw = helper1_dhw
                    await self._helper2_store.async_save({"helper2_pellets": self._helper2_pellets, "helper2_dhw": self._helper2_dhw})

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
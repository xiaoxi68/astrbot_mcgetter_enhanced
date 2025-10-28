import json
from pathlib import Path
import aiofiles
from typing import Dict, Any, Optional, Tuple, List
import time
from datetime import datetime
from astrbot.api import logger

# 统一使用 AstrBot 提供的日志系统

# 配置版本常量
CURRENT_VERSION = "2.3"
DEFAULT_CONFIG = {
    "version": CURRENT_VERSION,
    "next_id": 1,
    "servers": {},
    "last_cleanup": None,
    # 多服务器柱状图/趋势数据：{"<server_id>": {"history": [{"ts": int, "count": int}]}}
    "trends": {}
}

# 自动清理配置
AUTO_CLEANUP_DAYS = 10  # 10天未查询成功自动删除

def is_old_format(data: Dict[str, Any]) -> bool:
    """
    检查是否为旧版格式（直接以服务器名称为键）
    
    Args:
        data: 要检查的数据
        
    Returns:
        bool: 是否为旧版格式
    """
    if not data:
        return False
    
    # 检查是否有version字段
    if "version" in data:
        return False
    
    # 检查是否直接以服务器名称为键
    for key, value in data.items():
        if isinstance(value, dict) and "name" in value and "host" in value:
            return True
    
    return False

def migrate_old_format(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    将旧版格式迁移到新版格式
    
    Args:
        data: 旧版格式的数据
        
    Returns:
        Dict[str, Any]: 新版格式的数据
    """
    logger.info("检测到旧版配置格式，开始自动迁移...")
    
    new_data = DEFAULT_CONFIG.copy()
    next_id = 1
    
    for name, server_info in data.items():
        if isinstance(server_info, dict) and "name" in server_info and "host" in server_info:
            new_data["servers"][str(next_id)] = {
                "id": next_id,
                "name": server_info["name"],
                "host": server_info["host"]
            }
            next_id += 1
    
    new_data["next_id"] = next_id
    logger.info(f"迁移完成，共迁移 {len(data)} 个服务器配置")
    return new_data

async def write_json(json_path: str, new_data: Dict[str, Any]) -> None:
    """
    异步写入JSON数据到文件

    Args:
        json_path: JSON文件路径
        new_data: 要写入的数据字典

    Raises:
        IOError: 当文件写入失败时抛出
    """
    try:
        # 确保目录存在
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        
        # 异步写入，禁止转义
        async with aiofiles.open(json_path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(new_data, indent=4, ensure_ascii=False))
        logger.info(f"成功写入JSON文件: {json_path}")
    except Exception as e:
        logger.error(f"写入JSON文件失败: {e}")
        raise IOError(f"写入JSON文件失败: {e}")

async def read_json(json_path: str) -> Dict[str, Any]:
    """
    异步读取JSON文件内容，自动处理版本迁移

    Args:
        json_path: JSON文件路径

    Returns:
        解析后的JSON数据字典

    Raises:
        IOError: 当文件读取失败时抛出
        json.JSONDecodeError: 当JSON解析失败时抛出
    """
    try:
        if not Path(json_path).exists():
            logger.info(f"JSON文件不存在，创建新文件: {json_path}")
            await write_json(json_path=json_path, new_data=DEFAULT_CONFIG)
            return DEFAULT_CONFIG

        async with aiofiles.open(json_path, 'r', encoding='utf-8') as f:
            content = await f.read()
            # 避免在控制台输出完整 JSON 内容，改为精简日志
            logger.debug(f"读取到的JSON内容（{len(content)} 字节）")
            data = json.loads(content)
            
            # 检查是否为旧版格式，如果是则自动迁移
            if is_old_format(data):
                data = migrate_old_format(data)
                # 保存迁移后的数据
                await write_json(json_path, data)
                logger.info("旧版配置已自动迁移并保存")
            
            # 确保数据格式正确
            if "version" not in data:
                data["version"] = CURRENT_VERSION
            if "next_id" not in data:
                data["next_id"] = 1
            if "servers" not in data:
                data["servers"] = {}
            if "trends" not in data or not isinstance(data.get("trends"), dict):
                data["trends"] = {}

            # 迁移旧版单服趋势到多服结构
            if isinstance(data.get("trend"), dict) and data["trend"].get("server_id"):
                sid = str(data["trend"]["server_id"])
                hist = data["trend"].get("history", []) or []
                if sid:
                    data["trends"].setdefault(sid, {}).setdefault("history", [])
                    # 合并，按时间去重保留较新
                    existing = {int(h.get("ts", 0)): int(h.get("count", 0)) for h in data["trends"][sid]["history"]}
                    for h in hist:
                        ts = int(h.get("ts", 0))
                        existing[ts] = int(h.get("count", 0))
                    merged = [{"ts": ts, "count": cnt} for ts, cnt in sorted(existing.items())]
                    # 仅保留最近24条
                    if len(merged) > 24:
                        merged = merged[-24:]
                    data["trends"][sid]["history"] = merged
                # 清空旧字段（后续写回不会再包含）
                data.pop("trend", None)
            
            # 精简化的读取摘要，避免冗长JSON输出
            try:
                servers_cnt = len(data.get("servers", {}))
                trends_cnt = sum(len((v or {}).get("history", [])) for v in data.get("trends", {}).values())
                logger.info(f"成功读取JSON文件: {json_path}, servers={servers_cnt}, trends_points={trends_cnt}")
            except Exception:
                logger.info(f"成功读取JSON文件: {json_path}")
            return data
    except json.JSONDecodeError as e:
        logger.error(f"JSON解析失败: {e}, 文件内容: {content if 'content' in locals() else '无法读取'}")
        raise json.JSONDecodeError(f"JSON解析失败: {e}", e.doc, e.pos)
    except Exception as e:
        logger.error(f"读取JSON文件失败: {e}, 文件路径: {json_path}")
        raise IOError(f"读取JSON文件失败: {e}")

def get_server_by_name(data: Dict[str, Any], name: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    通过服务器名称查找服务器信息
    
    Args:
        data: 配置数据
        name: 服务器名称
        
    Returns:
        Optional[Tuple[str, Dict[str, Any]]]: (id, server_info) 或 None
    """
    servers = data.get("servers", {})
    for server_id, server_info in servers.items():
        if server_info.get("name") == name:
            return server_id, server_info
    return None

# 已废弃的按ID直接读 helper，使用 get_server_info 统一入口

async def add_data(json_path: str, name: str, host: str) -> bool:
    """
    向JSON文件添加新的服务器数据

    Args:
        json_path: JSON文件路径
        name: 服务器名称
        host: 服务器主机地址

    Returns:
        bool: 添加是否成功
    """
    try:
        data = await read_json(json_path)
        
        # 检查服务器名称是否已存在
        existing_server = get_server_by_name(data, name)
        if existing_server:
            logger.warning(f"服务器名称已存在: {name} (ID: {existing_server[0]})")
            return False

        # 分配新的ID：复用已删除形成的空洞，选择最小可用正整数
        servers = data.get("servers", {})
        used_ids = set()
        for k in servers.keys():
            try:
                used_ids.add(int(k))
            except (TypeError, ValueError):
                # 跳过非数字键，理论上不应出现
                continue

        new_id = 1
        while new_id in used_ids:
            new_id += 1

        server_id = str(new_id)

        # 更新 next_id 为当前已用最大ID+1，保持向后兼容
        max_used = max(used_ids) if used_ids else 0
        data["next_id"] = max(max_used, new_id) + 1
        
        # 添加新服务器
        current_time = int(time.time())
        data["servers"][server_id] = {
            "id": int(server_id),
            "name": name,
            "host": host,
            "created_time": current_time,
            "last_success_time": current_time,
            "last_failed_time": None,
            "failed_count": 0
        }
        
        await write_json(json_path, data)
        logger.info(f"成功添加服务器数据: {name} (ID: {server_id})")
        return True
    except Exception as e:
        logger.error(f"添加服务器数据失败: {e}")
        return False

async def del_data(json_path: str, identifier: str) -> bool:
    """
    从JSON文件中删除服务器数据（支持通过ID或名称删除）

    Args:
        json_path: JSON文件路径
        identifier: 要删除的服务器ID或名称

    Returns:
        bool: 删除是否成功
    """
    try:
        data = await read_json(json_path)
        servers = data.get("servers", {})
        trends_map = data.get("trends", {}) or {}
        
        # 首先尝试作为ID查找
        if identifier in servers:
            server_info = servers[identifier]
            del servers[identifier]
            await write_json(json_path, data)
            logger.info(f"成功删除服务器数据: {server_info['name']} (ID: {identifier})")
            return True
        
        # 如果不是ID，尝试作为名称查找
        existing_server = get_server_by_name(data, identifier)
        if existing_server:
            server_id, server_info = existing_server
            del servers[server_id]
            await write_json(json_path, data)
            logger.info(f"成功删除服务器数据: {server_info['name']} (ID: {server_id})")
            return True
        
        logger.warning(f"服务器不存在: {identifier}")
        return False
    except Exception as e:
        logger.error(f"删除服务器数据失败: {e}")
        return False

async def update_data(json_path: str, identifier: str, new_name: Optional[str] = None, new_host: Optional[str] = None) -> bool:
    """
    更新服务器数据（支持通过ID或名称更新）

    Args:
        json_path: JSON文件路径
        identifier: 要更新的服务器ID或名称
        new_name: 新的服务器名称（可选）
        new_host: 新的服务器主机地址（可选）

    Returns:
        bool: 更新是否成功
    """
    try:
        data = await read_json(json_path)
        servers = data.get("servers", {})
        
        # 查找服务器
        server_id = None
        server_info = None
        
        # 首先尝试作为ID查找
        if identifier in servers:
            server_id = identifier
            server_info = servers[identifier]
        else:
            # 如果不是ID，尝试作为名称查找
            existing_server = get_server_by_name(data, identifier)
            if existing_server:
                server_id, server_info = existing_server
        
        if not server_info:
            logger.warning(f"服务器不存在: {identifier}")
            return False
        
        # 检查新名称是否与其他服务器冲突
        if new_name and new_name != server_info["name"]:
            existing_server = get_server_by_name(data, new_name)
            if existing_server and existing_server[0] != server_id:
                logger.warning(f"服务器名称已存在: {new_name}")
                return False
        
        # 更新数据
        if new_name is not None:
            server_info["name"] = new_name
        if new_host is not None:
            server_info["host"] = new_host
        
        await write_json(json_path, data)
        logger.info(f"成功更新服务器数据: {server_info['name']} (ID: {server_id})")
        return True
    except Exception as e:
        logger.error(f"更新服务器数据失败: {e}")
        return False

async def get_all_servers(json_path: str) -> Dict[str, Dict[str, Any]]:
    """
    获取所有服务器信息

    Args:
        json_path: JSON文件路径

    Returns:
        Dict[str, Dict[str, Any]]: 所有服务器信息 {id: server_info}
    """
    try:
        data = await read_json(json_path)
        return data.get("servers", {})
    except Exception as e:
        logger.error(f"获取服务器列表失败: {e}")
        return {}

def _hour_bucket(ts: int) -> int:
    """按小时对齐的时间戳（整点）。"""
    return int(ts // 3600 * 3600)

async def append_trend_point(json_path: str, server_id: str, ts: int, count: int) -> bool:
    """为指定服务器追加或更新某整点的人数，最多保留24条。"""
    try:
        data = await read_json(json_path)
        trends = data.get("trends", {})
        trends.setdefault(str(server_id), {}).setdefault("history", [])
        history = trends[str(server_id)]["history"]
        ts_h = _hour_bucket(ts)
        if history and isinstance(history[-1], dict) and _hour_bucket(history[-1].get("ts", 0)) == ts_h:
            history[-1]["ts"] = ts_h
            history[-1]["count"] = int(count)
        else:
            history.append({"ts": ts_h, "count": int(count)})
        if len(history) > 24:
            history[:] = history[-24:]
        trends[str(server_id)]["history"] = history
        data["trends"] = trends
        await write_json(json_path, data)
        return True
    except Exception as e:
        logger.error(f"追加柱状图记录失败: {e}")
        return False

async def get_trend_history(json_path: str, server_id: str, hours: int = 24) -> Optional[List[Dict[str, Any]]]:
    """获取指定服务器的柱状图历史记录（最近N小时）。"""
    try:
        data = await read_json(json_path)
        trends = data.get("trends", {})
        hist = trends.get(str(server_id), {}).get("history", [])
        if hours > 0:
            hist = hist[-hours:]
        return hist
    except Exception as e:
        logger.error(f"获取柱状图历史失败: {e}")
        return None

async def get_all_trend_histories(json_path: str, hours: int = 24) -> Dict[str, List[Dict[str, Any]]]:
    """获取所有服务器的柱状图历史记录（最近N小时）。"""
    try:
        data = await read_json(json_path)
        trends = data.get("trends", {}) or {}
        result: Dict[str, List[Dict[str, Any]]] = {}
        for sid, obj in trends.items():
            hist = (obj or {}).get("history", [])
            if hours > 0:
                hist = hist[-hours:]
            result[str(sid)] = hist
        return result
    except Exception as e:
        logger.error(f"获取所有柱状图历史失败: {e}")
        return {}

async def update_server_status(json_path: str, identifier: str, success: bool) -> bool:
    """
    更新服务器查询状态

    Args:
        json_path: JSON文件路径
        identifier: 服务器ID或名称
        success: 查询是否成功

    Returns:
        bool: 更新是否成功
    """
    try:
        data = await read_json(json_path)
        servers = data.get("servers", {})
        
        # 查找服务器
        server_id = None
        server_info = None
        
        # 首先尝试作为ID查找
        if identifier in servers:
            server_id = identifier
            server_info = servers[identifier]
        else:
            # 如果不是ID，尝试作为名称查找
            existing_server = get_server_by_name(data, identifier)
            if existing_server:
                server_id, server_info = existing_server
        
        if not server_info:
            logger.warning(f"服务器不存在: {identifier}")
            return False
        
        current_time = int(time.time())
        
        if success:
            # 查询成功
            server_info["last_success_time"] = current_time
            server_info["failed_count"] = 0
            logger.info(f"更新服务器 {server_info['name']} (ID: {server_id}) 查询成功状态")
        else:
            # 查询失败
            server_info["last_failed_time"] = current_time
            server_info["failed_count"] = server_info.get("failed_count", 0) + 1
            logger.info(f"更新服务器 {server_info['name']} (ID: {server_id}) 查询失败状态，失败次数: {server_info['failed_count']}")
        
        await write_json(json_path, data)
        return True
    except Exception as e:
        logger.error(f"更新服务器状态失败: {e}")
        return False

async def auto_cleanup_servers(json_path: str) -> List[Dict[str, Any]]:
    """
    自动清理长时间未查询成功的服务器

    Args:
        json_path: JSON文件路径

    Returns:
        List[Dict[str, Any]]: 被删除的服务器列表
    """
    try:
        data = await read_json(json_path)
        servers = data.get("servers", {})
        
        if not servers:
            return []
        
        current_time = int(time.time())
        cutoff_time = current_time - (AUTO_CLEANUP_DAYS * 24 * 3600)  # 10天前的时间戳
        deleted_servers = []
        
        # 柱状图数据映射（用于计算最后有效成功时间）
        trends_map = data.get("trends", {}) or {}

        # 检查每个服务器
        servers_to_delete = []
        for server_id, server_info in servers.items():
            last_success_time = int(server_info.get("last_success_time", 0) or 0)
            # 同步考虑趋势记录的最新时间戳，若存在则视作近期成功采样
            latest_trend_ts = 0
            try:
                hist = (trends_map.get(str(server_id)) or {}).get("history", [])
                if hist:
                    latest_trend_ts = int(hist[-1].get("ts", 0) or 0)
            except Exception:
                latest_trend_ts = 0

            effective_last_ok = max(last_success_time, latest_trend_ts)
            # 如果“最后有效成功时间”超过10天，标记为删除
            if effective_last_ok < cutoff_time:
                servers_to_delete.append((server_id, server_info))
        
        # 删除标记的服务器
        for server_id, server_info in servers_to_delete:
            del servers[server_id]
            deleted_servers.append({
                "id": server_id,
                "name": server_info["name"],
                "host": server_info["host"],
                "last_success_time": server_info.get("last_success_time"),
                "failed_count": server_info.get("failed_count", 0)
            })
            logger.info(f"自动删除长时间未查询成功的服务器: {server_info['name']} (ID: {server_id})")
        
        if deleted_servers:
            # 更新最后清理时间
            data["last_cleanup"] = current_time
            await write_json(json_path, data)
            logger.info(f"自动清理完成，删除了 {len(deleted_servers)} 个服务器")
        
        return deleted_servers
    except Exception as e:
        logger.error(f"自动清理服务器失败: {e}")
        return []

async def get_server_info(json_path: str, identifier: str) -> Optional[Dict[str, Any]]:
    """
    获取指定服务器的信息（支持通过ID或名称查找）

    Args:
        json_path: JSON文件路径
        identifier: 服务器ID或名称

    Returns:
        Optional[Dict[str, Any]]: 服务器信息或None
    """
    try:
        data = await read_json(json_path)
        servers = data.get("servers", {})
        
        # 首先尝试作为ID查找
        if identifier in servers:
            return servers[identifier]
        
        # 如果不是ID，尝试作为名称查找
        existing_server = get_server_by_name(data, identifier)
        if existing_server:
            return existing_server[1]
        
        return None
    except Exception as e:
        logger.error(f"获取服务器信息失败: {e}")
        return None

from typing import List, Optional, Dict, Any
from pathlib import Path
import astrbot.core.message.components as Comp
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger
from .script.get_server_info import get_server_status
from .script.get_img import generate_server_info_image
from .script.bar_chart import generate_bar_chart_image
from .script.json_operate import (
    read_json, add_data, del_data, update_data, 
    get_all_servers, get_server_info, get_server_by_name,
    update_server_status, auto_cleanup_servers,
    append_trend_point, get_trend_history, get_all_trend_histories
)
import asyncio
import re
from datetime import datetime, timedelta

# 常量定义
HELP_INFO = """
mchelp 
--查看帮助

/mc   
--查询保存的服务器

/mcadd 服务器名称 服务器地址 [force]
--添加要查询的服务器
--force: 可选参数，设为True时跳过预查询检查强制添加

/mcget 服务器名称/ID
--获取指定服务器的地址信息

/mcdel 服务器名称/ID 
--删除服务器

/mcup 服务器名称/ID [新名称] [新地址]
--更新服务器信息

/mclist
--列出所有服务器及其ID

/mccleanup
--手动触发自动清理（删除10天未查询成功的服务器）

/mcdata [服务器名称/ID] [小时数=24]
--输出当前群全部或指定服务器在最近N小时的在线人数柱状图
"""

@register("astrbot_mcgetter_enhanced", "薄暝", "查询mc服务器信息和玩家列表,在线人数柱状图,渲染为图片(修改自QiChen的mcgetter)", "1.2.0")
class MyPlugin(Star):
    """Minecraft服务器信息查询插件"""
    
    def __init__(self, context: Context):
        """
        初始化插件

        Args:
            context: 插件上下文
        """
        super().__init__(context)
        logger.info("MyPlugin 初始化完成")
        # 启动每小时柱状图数据采样后台任务（单例，默认对所有已配置服务器启用）
        self._trend_task: Optional[asyncio.Task] = None
        if getattr(self, "_trend_task", None) is None:
            self._trend_task = asyncio.create_task(self._bar_data_loop())

    @filter.command("mchelp")
    async def get_help(self, event: AstrMessageEvent) -> MessageEventResult:
        """
        显示帮助信息

        Args:
            event: 消息事件

        Returns:
            包含帮助信息的消息结果
        """
        yield event.plain_result(HELP_INFO)

    @filter.command("mc")
    async def mcgetter(self, event: AstrMessageEvent) -> Optional[MessageEventResult]:
        """
        查询所有保存的服务器信息

        Args:
            event: 消息事件

        Returns:
            包含服务器信息图片的消息结果，如果出错则返回None
        """
        logger.info("开始执行 mc 命令")
        try:
            group_id = event.get_group_id()
            logger.info(f"获取到群组ID: {group_id}")
            
            json_path = await self.get_json_path(group_id)
            logger.info(f"JSON文件路径: {json_path}")
            
            json_data = await read_json(json_path)
            logger.info(f"读取到的JSON数据: {json_data}")
            
            if not json_data or not json_data.get("servers"):
                logger.warning("JSON数据为空或没有服务器")
                yield event.plain_result("请先使用 /mcadd 添加服务器")
                return
            
            message_chain: List[Comp.Image] = []
            servers = json_data.get("servers", {})
            
            for server_id, server_info in servers.items():
                try:
                    logger.info(f"正在处理服务器: {server_info['name']} (ID: {server_id}), 信息: {server_info}")
                    mcinfo_img = await self.get_img(server_info['name'], server_info['host'], server_id, str(json_path))
                    if mcinfo_img:
                        message_chain.append(Comp.Image.fromBase64(mcinfo_img))
                        logger.info(f"成功添加图片到消息链，服务器名称: {server_info['name']} (ID: {server_id})")
                    else:
                        logger.warning(f"获取服务器 {server_info['name']} (ID: {server_id}) 的图片失败")
                except Exception as e:
                    logger.error(f"处理服务器 {server_info['name']} (ID: {server_id}) 时出错: {e}")
                    continue

            # 查询更新完成后再执行自动清理，避免误删刚成功的服务器
            deleted_servers = await auto_cleanup_servers(json_path)
            if deleted_servers:
                cleanup_message = "自动清理完成，以下服务器因10天未查询成功已被删除:\n"
                for server in deleted_servers:
                    last_success_date = datetime.fromtimestamp(server['last_success_time']).strftime('%Y-%m-%d %H:%M:%S')
                    cleanup_message += f"• {server['name']} (ID: {server['id']}) - 地址: {server['host']} - 最后成功: {last_success_date}\n"
                # 先发送查询结果，再提示清理
                if message_chain:
                    yield event.chain_result(message_chain)
                yield event.plain_result(cleanup_message.strip())
                return

            if message_chain:
                logger.info(f"成功生成消息链，包含 {len(message_chain)} 张图片")
                yield event.chain_result(message_chain)
            else:
                logger.warning("没有可用的服务器信息")
                yield event.plain_result("没有可用的服务器信息，请检查服务器是否在线")
                
        except Exception as e:
            logger.error(f"执行 mc 命令时出错: {e}")
            yield event.plain_result("查询服务器信息时发生错误")

    @filter.command("mcadd")
    async def mcadd(self, event: AstrMessageEvent, name: str, host: str, force: bool = False) -> MessageEventResult:
        """
        添加新的服务器

        Args:
            event: 消息事件
            name: 服务器名称
            host: 服务器地址
            force: 是否强制添加（跳过预查询检查）

        Returns:
            操作结果消息
        """
        logger.info(f"开始执行 mcadd 命令: {name} -> {host}, force: {force}")
        
        try:
            # 检查host合法性
            if not re.match(r'^[a-zA-Z0-9.:-]+$', host):
                yield event.plain_result("服务器地址格式不正确，只能包含字母、数字和符号.:-")
                return
            elif await get_server_status(host) is None and not force:
                yield event.plain_result("预查询失败，请检查服务器是否在线或地址是否正确，或在完整的/mcadd命令后加上True 强制添加")
                return
                
            group_id = event.get_group_id()
            json_path = await self.get_json_path(group_id)
            
            # 检查当前地址是否已存在
            try:
                json_data = await read_json(json_path)
                servers = json_data.get("servers", {})
                if servers:
                    for server_id, server_info in servers.items():
                        if server_info['host'] == host:
                            yield event.plain_result(f"已存在相同地址的服务器 {server_info['name']} (ID: {server_id})")
                            return
            except Exception as e:
                logger.error(f"检查服务器地址时出错: {e}")
                yield event.plain_result("检查服务器地址时发生错误")
                return
                
            if await add_data(json_path, name, host):
                # 获取新添加的服务器ID
                json_data = await read_json(json_path)
                servers = json_data.get("servers", {})
                for server_id, server_info in servers.items():
                    if server_info['name'] == name and server_info['host'] == host:
                        yield event.plain_result(f"成功添加服务器 {name} (ID: {server_id})")
                        return
                yield event.plain_result(f"成功添加服务器 {name}")
            else:
                yield event.plain_result(f"无法添加 {name}，请检查是否已存在")
                
        except Exception as e:
            logger.error(f"执行 mcadd 命令时出错: {e}")
            yield event.plain_result("添加服务器时发生错误")

    @filter.command("mcdel")
    async def mcdel(self, event: AstrMessageEvent, identifier: str) -> MessageEventResult:
        """
        删除指定的服务器（支持通过名称或ID删除）

        Args:
            event: 消息事件
            identifier: 要删除的服务器名称或ID

        Returns:
            操作结果消息
        """
        logger.info(f"开始执行 mcdel 命令: {identifier}")
        try:
            group_id = event.get_group_id()
            json_path = await self.get_json_path(group_id)
            
            if await del_data(json_path, identifier):
                yield event.plain_result(f"成功删除服务器 {identifier}")
            else:
                yield event.plain_result(f"无法删除 {identifier}，请检查是否存在")
                
        except Exception as e:
            logger.error(f"执行 mcdel 命令时出错: {e}")
            yield event.plain_result("删除服务器时发生错误")

    @filter.command("mcget")
    async def mcget(self, event: AstrMessageEvent, identifier: str) -> MessageEventResult:
        """
        获取指定服务器的信息（支持通过名称或ID查找）
        """
        logger.info(f"开始执行 mcget 命令: {identifier}")
        try:
            group_id = event.get_group_id()
            json_path = await self.get_json_path(group_id)
            
            server_info = await get_server_info(json_path, identifier)
            if not server_info:
                yield event.plain_result(f"没有找到服务器 {identifier}")
                return
                
            yield event.plain_result(f"{server_info['name']} (ID: {server_info['id']}) 的地址是:")
            yield event.plain_result(f"{server_info['host']}")
            
        except Exception as e:
            logger.error(f"执行 mcget 命令时出错: {e}")
            yield event.plain_result("获取服务器信息时发生错误")

    @filter.command("mcup")
    async def mcup(self, event: AstrMessageEvent, identifier: str, new_name: Optional[str] = None, new_host: Optional[str] = None) -> MessageEventResult:
        """
        更新服务器信息（支持通过名称或ID更新）

        Args:
            event: 消息事件
            identifier: 要更新的服务器名称或ID
            new_name: 新的服务器名称（可选）
            new_host: 新的服务器地址（可选）

        Returns:
            操作结果消息
        """
        logger.info(f"开始执行 mcup 命令: {identifier}, new_name: {new_name}, new_host: {new_host}")
        
        try:
            if not new_name and not new_host:
                yield event.plain_result("请提供要更新的信息（新名称或新地址）")
                return
                
            # 如果提供了新地址，检查格式
            if new_host and not re.match(r'^[a-zA-Z0-9.:-]+$', new_host):
                yield event.plain_result("服务器地址格式不正确，只能包含字母、数字和符号.:-")
                return
                
            group_id = event.get_group_id()
            json_path = await self.get_json_path(group_id)
            
            if await update_data(json_path, identifier, new_name, new_host):
                # 获取更新后的服务器信息
                updated_info = await get_server_info(json_path, identifier)
                if updated_info:
                    yield event.plain_result(f"成功更新服务器信息: {updated_info['name']} (ID: {updated_info['id']})")
                else:
                    yield event.plain_result(f"成功更新服务器 {identifier}")
            else:
                yield event.plain_result(f"无法更新 {identifier}，请检查是否存在或名称是否冲突")
                
        except Exception as e:
            logger.error(f"执行 mcup 命令时出错: {e}")
            yield event.plain_result("更新服务器信息时发生错误")

    @filter.command("mclist")
    async def mclist(self, event: AstrMessageEvent) -> MessageEventResult:
        """
        列出所有服务器及其ID
        """
        logger.info("开始执行 mclist 命令")
        try:
            group_id = event.get_group_id()
            json_path = await self.get_json_path(group_id)
            
            servers = await get_all_servers(json_path)
            if not servers:
                yield event.plain_result("没有保存的服务器")
                return
                
            server_list = "当前保存的服务器列表:\n"
            for server_id, server_info in servers.items():
                server_list += f"ID: {server_id}, 名称: {server_info['name']}, 地址: {server_info['host']}\n"
                
            yield event.plain_result(server_list.strip())
            
        except Exception as e:
            logger.error(f"执行 mclist 命令时出错: {e}")
            yield event.plain_result("获取服务器列表时发生错误")

    @filter.command("mccleanup")
    async def mccleanup(self, event: AstrMessageEvent) -> MessageEventResult:
        """
        手动触发自动清理（删除10天未查询成功的服务器）
        """
        logger.info("开始执行 mccleanup 命令")
        try:
            group_id = event.get_group_id()
            json_path = await self.get_json_path(group_id)
            
            deleted_servers = await auto_cleanup_servers(json_path)
            if deleted_servers:
                cleanup_message = "自动清理完成，以下服务器因10天未查询成功已被删除:\n"
                for server in deleted_servers:
                    last_success_date = datetime.fromtimestamp(server['last_success_time']).strftime('%Y-%m-%d %H:%M:%S')
                    cleanup_message += f"• {server['name']} (ID: {server['id']}) - 地址: {server['host']} - 最后成功: {last_success_date}\n"
                yield event.plain_result(cleanup_message.strip())
            else:
                yield event.plain_result("没有需要清理的服务器")
                
        except Exception as e:
            logger.error(f"执行 mccleanup 命令时出错: {e}")
            yield event.plain_result("自动清理时发生错误")

    @filter.command("mcdata")
    async def mcdata(self, event: AstrMessageEvent, identifier: Optional[str] = None, hours: int = 24) -> Optional[MessageEventResult]:
        """输出当前群全部或指定服务器最近N小时（默认24）的在线人数柱状图。"""
        try:
            group_id = event.get_group_id()
            json_path = await self.get_json_path(group_id)
            servers = await get_all_servers(str(json_path))
            if not servers:
                yield event.plain_result("当前群无已配置服务器，请先使用 /mcadd 添加。")
                return

            logger.info(f"mcdata 参数: identifier={identifier!r}, hours={hours!r}")

            # 解析参数：
            # - 单参数为纯数字且没有同ID服务器时 → 作为小时数（全部服务器）
            # - 否则 → 作为服务器名称/ID（统一转为字符串）
            if identifier is not None:
                ident_str = str(identifier)
                if ident_str.isdigit():
                    maybe = await get_server_info(str(json_path), ident_str)
                    if maybe is None:
                        # 视为小时数
                        try:
                            hours = int(ident_str)
                            identifier = None
                        except Exception:
                            identifier = ident_str
                    else:
                        identifier = ident_str
                else:
                    identifier = ident_str

            # 规范化 hours 范围
            try:
                hours = int(hours)
            except Exception:
                hours = 24
            hours = max(1, min(168, hours))
            logger.info(f"mcdata 解析后: target={'ALL' if not identifier else identifier}, hours={hours}")

            images: List[Comp.Image] = []
            if identifier:
                # 单服务器模式
                try:
                    sinfo = await get_server_info(str(json_path), identifier)
                    if not sinfo:
                        yield event.plain_result(f"没有找到服务器 {identifier}")
                        return
                    sid = str(sinfo.get("id"))
                    name = sinfo.get("name", f"ID:{sid}")
                    # 与 mc 行为对齐：当前不可达则跳过
                    host = sinfo.get("host")
                    status_now = await get_server_status(host) if host else None
                    if not status_now:
                        yield event.plain_result(f"{name} 当前不可达，已跳过")
                        return
                    hist = await get_trend_history(str(json_path), sid, hours=hours)
                    img_b64 = generate_bar_chart_image(hist or [], name, hours=hours)
                    images.append(Comp.Image.fromBase64(img_b64))
                except Exception as ie:
                    logger.error(f"mcdata 单服生成失败: id={identifier}, hours={hours}, err={ie}")
                    raise
            else:
                # 全部服务器模式
                try:
                    all_hist = await get_all_trend_histories(str(json_path), hours=hours)
                    for sid, sinfo in servers.items():
                        name = sinfo.get("name", f"ID:{sid}")
                        host = sinfo.get("host")
                        # 与 mc 行为对齐：当前不可达则跳过该服
                        try:
                            status_now = await get_server_status(host) if host else None
                        except Exception as ie:
                            logger.debug(f"mcdata 全服检测失败: {name} host={host} err={ie}")
                            status_now = None
                        if not status_now:
                            continue
                        hist = all_hist.get(str(sid), [])
                        img_b64 = generate_bar_chart_image(hist or [], name, hours=hours)
                        images.append(Comp.Image.fromBase64(img_b64))
                except Exception as ie:
                    logger.error(f"mcdata 全服生成失败: hours={hours}, err={ie}")
                    raise

            if images:
                yield event.chain_result(images)
            else:
                yield event.plain_result("暂无柱状图数据，稍后再试。")
        except Exception as e:
            logger.error(f"生成柱状图失败: {e}")
            yield event.plain_result("生成柱状图失败，请稍后再试。")

    async def get_img(self, server_name: str, host: str, server_id: Optional[str] = None, json_path: Optional[str] = None) -> Optional[str]:
        """
        获取服务器信息图片

        Args:
            server_name: 服务器名称
            host: 服务器地址
            server_id: 服务器ID（可选）
            json_path: JSON文件路径（用于更新状态）

        Returns:
            图片的base64编码字符串，如果获取失败则返回None
        """
        logger.info(f"开始获取服务器 {server_name} 的图片，主机地址: {host}")
        try:
            info = await get_server_status(host)
            if not info:
                logger.error(f"无法获取服务器 {server_name} 的状态信息")
                # 更新查询失败状态
                if json_path and server_id:
                    await update_server_status(json_path, server_id, False)
                return None

            # 更新查询成功状态
            if json_path and server_id:
                await update_server_status(json_path, server_id, True)

            # 默认对所有服务器记录小时数据：出现异常记录到日志便于排查
            try:
                if json_path and server_id:
                    await append_trend_point(json_path, str(server_id), int(datetime.now().timestamp()), int(info['plays_online']))
            except Exception as e:
                logger.warning(f"追加柱状图数据失败 group={json_path}, sid={server_id}: {e}")

            info['server_name'] = server_name
            # 如果有服务器ID，则在名称前添加ID
            display_name = f"[{server_id}]{server_name}" if server_id else server_name
            
            mcinfo_img = await generate_server_info_image(
                players_list=info['players_list'],
                latency=info['latency'],
                server_name=display_name,
                plays_max=info['plays_max'],
                plays_online=info['plays_online'],
                server_version=info['server_version'],
                icon_base64=info['icon_base64'],
                host_address=info.get('host', host)
            )
            logger.info(f"成功生成服务器 {server_name} 的图片")
            return mcinfo_img
            
        except Exception as e:
            logger.error(f"获取服务器 {server_name} 的图片时出错: {e}")
            # 更新查询失败状态
            if json_path and server_id:
                await update_server_status(json_path, server_id, False)
            return None

    async def get_json_path(self, group_id: str) -> Path:
        """
        获取群组的JSON配置文件路径

        Args:
            group_id: 群组ID

        Returns:
            JSON文件的Path对象
        """
        data_path = StarTools.get_data_dir("astrbot_mcgetter")
        json_path = data_path / f'{group_id}.json'
        json_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"群号 {group_id} 的 JSON 文件路径: {json_path}")
        return json_path

    async def _bar_data_loop(self):
        """每小时扫描所有群配置，按 host 去重采样一次并写回所有群，保证跨群一致。"""
        while True:
            try:
                data_dir = StarTools.get_data_dir("astrbot_mcgetter")
                host_map: Dict[str, list] = {}
                if data_dir.exists():
                    # 先构建 host → [(json_path, sid), ...] 的映射
                    for p in data_dir.glob("*.json"):
                        try:
                            data = await read_json(str(p))
                            servers = data.get("servers", {})
                            for sid, sinfo in servers.items():
                                host = (sinfo or {}).get("host")
                                if not host:
                                    continue
                                host_map.setdefault(str(host), []).append((str(p), str(sid)))
                        except Exception as e:
                            logger.warning(f"数据采样预处理失败: {p.name}: {e}")

                # 逐 host 采样一次，并写回所有关联群文件
                now_ts = int(datetime.now().timestamp())
                for host, targets in host_map.items():
                    try:
                        status = await get_server_status(host)
                        if status and isinstance(status.get("plays_online"), int):
                            cnt = int(status["plays_online"])
                            for json_path, sid in targets:
                                try:
                                    await append_trend_point(json_path, sid, now_ts, cnt)
                                except Exception as ie:
                                    logger.debug(f"写入柱状图数据失败 host={host} file={json_path} sid={sid}: {ie}")
                    except Exception as ie:
                        logger.debug(f"host 采样失败 host={host}: {ie}")

                # 计算距离下个整点的秒数
                now = datetime.now()
                next_hour = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
                sleep_seconds = max(10, int((next_hour - now).total_seconds()))
                await asyncio.sleep(sleep_seconds)
            except Exception as e:
                logger.error(f"数据采样循环异常: {e}")
                await asyncio.sleep(300)

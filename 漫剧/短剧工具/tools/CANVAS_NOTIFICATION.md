# 画布批量通知与活动调度

2026-11-08：用户授权实现「本批审核完成，通知总导演」。**当前真实制作仍暂停，本轮不向真实五岗发消息。**

## 用户操作

先保存各项通过、修改意见、暂缓和补充意见；审完本次内容后，点击一次「本批审核完成，通知总导演」。通知区展示本批条数、排队、投递和导演接收状态。

未保存意见或还在勾选中的决定会阻止整批提交。所有已保存且尚未通知的记录合并进本批，不受列表筛选范围影响；上次入队后新保存的决定属于下一批。相同请求重试不重复创建，刷新/重启不清队列。

「待投递」只是本地保存。「已投递」需要真实会话工具受理身份；「总导演已接收」还要取得匹配的完整final回执。未知投递不自动重发。当前通知仅要求读取审核并回报接收，不恢复制作或追加消费。

## 本地文件与边界

- 原审核：作品 `协作/审核/记录/`。
- 通知及状态：作品 `协作/审核/通知记录/`，只追加。
- 活动调度租约：`协作/审核/通知监听状态.json`，超时或函数结束显示离线。
- 实际会话工具观察：`协作/审核/投递证据/`，新文件保存，不覆盖旧证据。
- 收件人来自该作品经校验的 `协作/工作区.json`、`协作/会话.json` 总导演绑定；浏览器不能传入其他threadId。

新增 `POST /api/review-notifications` 沿用本机同源、会话令牌、长度限制，仅接受作品键、请求编号和本批指纹，只入队。没有HTTP领取、投递、回执或执行接口。

```powershell
python -I -X utf8 tools/canvas_notify.py --project "作品绝对目录" status
python -I -X utf8 tools/canvas_notify.py --project "作品绝对目录" wait --owner "调度唯一编号" --timeout 30
python -I -X utf8 tools/canvas_notify.py --project "作品绝对目录" stop --owner "调度唯一编号"
```

CLI只本地等待/领取，不能发消息。不要把独立后台Python进程当作已接通Codex。

## 活动调度接入

`canvas_notify_controller.js` 提供活动Codex统一调度的适配函数，参数为真实工具对象及固定作品配置。它使用当前会话已有的 `send_message_to_thread`、`wait_threads`、`read_thread`，不运行Agent CLI、不需要新推理密钥。

后续获准进入真实案例时，在 `functions.exec` 中加载工具目录里已审查的适配器并调用：

```javascript
const code = await tools.exec_command({
  cmd: "Get-Content -Raw -LiteralPath 'D:\\ai剧集制作\\漫剧\\短剧工具\\tools\\canvas_notify_controller.js'",
  max_output_tokens: 5000
});
if (code.exit_code !== 0) throw new Error('无法读取适配器');
const run = new Function('return (' + code.output + ')')();
text(await run(tools, {
  project: '<当前已授权作品的绝对目录>',
  toolRoot: 'D:\\ai剧集制作\\漫剧\\短剧工具',
  owner: '<调度唯一编号>', timeoutSeconds: 30
}));
```

**当前不要对真实作品运行投递示例。** 真实案例工作回合里，调度保持有界等待，用户只在画布点击；函数返回后会关闭在线状态，要继续等待时由活动调度进入下一轮。调度回合结束、电脑休眠时，页面显示离线，通知留队，不会凭空唤醒会话。

适配器先只读核对之前待接收/待核实的原会话，不重发，然后领取新批次。实际工具返回若无法核实新turn，仅标未知。接收回执必须匹配thread/host、通知ID、作品ID、本批指纹和审核记录摘要；纯文字“收到”、截断、别的作品或模拟路由不能算真实接收。

回执字段为 `schemaVersion:1`、`status:received`、`notificationId`、`workId`、`batchFingerprint`、`reviewRecordsDigest`。接收不等于质量通过或制作完成。详细取舍见 [ADR](../规划/2026-11-08-画布批量通知ADR.md)。

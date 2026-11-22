async function runCanvasNotificationController(tools, config) {
  // Loaded by the active Codex controller, with its actual tools object.
  // No HTTP app endpoint, external inference API, or agent CLI is used.
  if (!config || !config.project || !config.toolRoot || !/^[A-Za-z0-9_.:-]+$/.test(config.owner || ''))
    throw new Error('Explicit project, tool root and controller owner are required');
  const quote = value => "'" + String(value).replace(/'/g, "''") + "'";
  const python = config.python || 'python';
  const entry = config.toolRoot + '/tools/canvas_notify.py';
  async function command(action, args = [], allowPending = false) {
    const cmd = [python, '-I', '-X', 'utf8', entry, '--project', config.project, action, ...args].map(quote).join(' ');
    let result = await tools.exec_command({cmd: '& ' + cmd, workdir: config.toolRoot, max_output_tokens: 3500});
    let output = result.output || '';
    while (result.session_id) {
      result = await tools.write_stdin({session_id: result.session_id, chars: '', yield_time_ms: 10000, max_output_tokens: 3500});
      output += result.output || '';
    }
    if (result.exit_code !== 0) {
      if (allowPending) return {state: 'awaiting_receipt'};
      throw new Error('Local notification operation failed; no automatic resend: ' + output.slice(0, 800));
    }
    return JSON.parse(output);
  }
  async function observation(id, label, body) {
    if (!/^[a-f0-9]{32}$/.test(id)) throw new Error('Invalid notification ID');
    const relative = '协作/审核/投递证据/' + id + '-' + label + '-' + Date.now() + '.json';
    // ASCII JSON inside a PowerShell literal here-string; never interpolate a
    // model/tool response into executable shell code or a command substitution.
    const payload = JSON.stringify({root: config.project, relative, body}).replace(/[^\x20-\x7e]|'/g,
      c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
    const script = "import json,sys\nfrom pathlib import Path\n" +
      "v=json.loads(r'''" + payload + "''')\n" +
      'root=Path(v["root"]).resolve()\np=(root/v["relative"]).resolve()\n' +
      'assert p.is_relative_to(root)\np.parent.mkdir(parents=True,exist_ok=True)\n' +
      'with p.open("x",encoding="utf-8") as f: json.dump(v["body"],f,ensure_ascii=True)\n';
    const result = await tools.exec_command({cmd: "@'\n" + script + "\n'@ | & " + quote(python) + ' -I -X utf8 -',
      workdir: config.toolRoot, max_output_tokens: 500});
    if (result.exit_code !== 0 || result.session_id) throw new Error('Observation save incomplete; no automatic resend');
    return relative;
  }
  async function reconcile(item) {
    if (item.route.simulation) throw new Error('Synthetic route cannot be sent to real app tools');
    const response = await tools.mcp__codex_app__read_thread({threadId:item.route.threadId,hostId:item.route.hostId,
      turnLimit:2,includeOutputs:false,maxOutputCharsPerItem:16000});
    const path = await observation(item.notificationId, 'read', response);
    return command('receipt', ['--notification',item.notificationId,'--observation',path], true);
  }
  // Reconcile prior uncertain delivery by reading the original thread. Never
  // send that notification again, including after a controller restart.
  const pending = await command('pending-receipts');
  for (const item of pending.items.slice(0, 4)) await reconcile(item);
  let claimed;
  try {
    claimed = await command('wait', ['--owner',config.owner,'--timeout',String(config.timeoutSeconds ?? 30)]);
    if (claimed.state !== 'claimed') return claimed;
    if (claimed.route.simulation) throw new Error('Synthetic route cannot be sent to real app tools');
    let response;
    try {
      response = await tools.mcp__codex_app__send_message_to_thread({threadId:claimed.route.threadId,
        hostId:claimed.route.hostId,prompt:claimed.prompt});
    } catch (_) {
      // The app may have accepted a request before a connection failure.
      response = {isError:true,content:[],controllerOutcome:'unknown'};
    }
    const path = await observation(claimed.notificationId, 'send', response);
    const delivery = await command('delivery', ['--notification',claimed.notificationId,'--claim',claimed.claimId,'--observation',path]);
    await tools.mcp__codex_app__wait_threads({targets:[{threadId:claimed.route.threadId,hostId:claimed.route.hostId}],timeoutMs:30000});
    const receipt = await reconcile(claimed);
    return {...delivery, receipt:receipt.state};
  } finally {
    // A returned tool cell is no longer an active listener. Do not leave a
    // permanently green online badge after the controller turn has finished.
    if (claimed) await command('stop', ['--owner',config.owner]);
  }
}

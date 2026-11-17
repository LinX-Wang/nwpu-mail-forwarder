/* Local, dependency-free presentation layer. Existing APIs and forms remain authoritative. */
'use strict';
(() => {
  const body = document.body;
  const $ = (selector, root = document) => root.querySelector(selector);
  const all = (selector, root = document) => [...root.querySelectorAll(selector)];
  const csrf = $('meta[name="csrf-token"]').content;
  const labels = {
    pending: '等待发送', sending: '发送中', retry: '等待重试', failed: '失败', sent: '成功',
    duplicate: '已关联旧任务', needs_review: '时间异常，待确认', processing: '处理中', done: '完成',
    success: '成功', test_imap: '学校连接测试', test_smtp: 'QQ SMTP 测试', test_mail: '发送测试邮件',
    authentication_failed: '认证失败，请在终端更新凭据并重启服务',
    configuration_required: '请完成配置和凭据设置', network_timeout: '网络超时，将自动重试',
    network_or_storage_failed: '网络或本地存储异常', tls_failed: 'TLS 证书或连接验证失败',
    smtp_permanent: 'SMTP 拒绝发送，请检查配置后重试', smtp_temporary: 'SMTP 暂时不可用，将自动重试',
    recipient_rejected: '收件地址被拒绝', delivery_uncertain: '发送结果不明，将使用同一任务 ID 重试',
    size_limit_notification: '超过大小限制，已改为简短通知', operation_failed: '操作失败，请查看脱敏日志',
    interrupted: '测试被重启中断，请重新测试'
  };
  const label = value => labels[value] || value || '—';
  const formatTime = value => value ? new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    second: '2-digit', hour12: false
  }).format(new Date(value * 1000)).replaceAll('/', '-') : '—';
  function node(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = text;
    return result;
  }
  function toast(message, tone = 'info') {
    const item = node('div', 'toast toast-' + tone, message);
    $('#toast-stack').append(item);
    setTimeout(() => item.remove(), 5500);
  }
  function badge(text, tone = 'neutral', dot = true) {
    const result = node('span', 'badge badge-' + tone);
    if (dot) result.append(node('span', 'status-dot'));
    result.append(document.createTextNode(text));
    return result;
  }
  function buttonProgress(button, text) {
    const original = [...button.childNodes];
    const content = node('span', 'button-content'); content.append(...original);
    const progress = node('span', 'button-progress');
    progress.append(node('span', 'spinner'), document.createTextNode(text));
    button.replaceChildren(content, progress);
    button.classList.add('button-loading'); button.setAttribute('aria-busy', 'true');
    return () => {
      button.replaceChildren(...original); button.classList.remove('button-loading');
      button.removeAttribute('aria-busy');
    };
  }
  function smallIcon(check) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
    for (const [key,value] of Object.entries({class:'icon',viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.75','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'})) svg.setAttribute(key,value);
    const path=document.createElementNS('http://www.w3.org/2000/svg','path');
    path.setAttribute('d',check?'m5 12 4 4L19 6':'M12 8v5m0 3v.01M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18');
    svg.append(path); return svg;
  }
  function updateText(selector, text) {
    all(selector).forEach(el => { if (el.textContent !== String(text)) el.textContent = text; });
  }
  function updateBadge(selector, text, tone) {
    all(selector).forEach(el => {
      if (el.dataset.current === text + tone) return;
      el.replaceChildren(badge(text, tone));
      el.dataset.current = text + tone;
      el.classList.remove('changed'); requestAnimationFrame(() => el.classList.add('changed'));
    });
  }
  async function confirmAction(description, title = '确认操作') {
    const dialog = $('#confirm-dialog');
    if (!dialog || !dialog.showModal) return window.confirm(description);
    if (dialog.open) return false;
    $('#confirm-title').textContent = title;
    $('#confirm-description').textContent = description;
    dialog.returnValue = 'cancel';
    dialog.showModal();
    return new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), {once: true}));
  }
  const menuButton = $('[data-toggle-nav]');
  function setNav(open) {
    body.classList.toggle('nav-open', open);
    menuButton?.setAttribute('aria-expanded', String(open));
    if (open) $('.nav-item.active')?.focus(); else menuButton?.focus();
  }
  menuButton?.addEventListener('click', () => setNav(!body.classList.contains('nav-open')));
  $('[data-close-nav]')?.addEventListener('click', () => setNav(false));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && body.classList.contains('nav-open')) setNav(false);
    if (event.key === 'Tab' && body.classList.contains('nav-open')) {
      const focusable = all('a, button', $('#sidebar'));
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { last.focus(); event.preventDefault(); }
      if (!event.shiftKey && document.activeElement === last) { first.focus(); event.preventDefault(); }
    }
  });
  window.matchMedia('(min-width: 761px)').addEventListener('change', e => { if (e.matches) { body.classList.remove('nav-open'); menuButton?.setAttribute('aria-expanded','false'); } });
  let sessionExpired = false;
  async function request(url, options = {}) {
    if (sessionExpired) throw new Error('session-expired');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(url, {...options, credentials: 'same-origin', cache: 'no-store', signal: controller.signal});
      if (response.redirected && new URL(response.url).pathname.endsWith('/login')) {
        sessionExpired = true;
        window.location.replace(response.url); throw new Error('session-expired');
      }
      if (!response.ok) throw new Error('request-failed');
      return response;
    } finally { clearTimeout(timeout); }
  }
  function emptyState(title, description) {
    const result = node('div', 'empty-state');
    result.append(node('span', 'empty-icon', '—'), node('h3', '', title), node('p', '', description));
    return result;
  }
  const toneFor = status => ['sent','done','duplicate'].includes(status) ? 'success' : status === 'failed' ? 'danger' : ['retry','needs_review','pending'].includes(status) ? 'warning' : 'info';
  function renderCommands(commands) {
    const list = $('[data-commands]');
    if (!list) return;
    const signature = JSON.stringify(commands);
    if (list.dataset.signature === signature) return;
    list.dataset.signature = signature;
    updateText('[data-command-count]', commands.length + ' 条');
    const rows = commands.map(c => {
      const tone = c.result === 'success' ? 'success' : c.status === 'failed' ? 'danger' : 'warning';
      const row = node('article','activity-item');
      const marker=node('span','activity-marker ' + tone); marker.append(smallIcon(c.result==='success')); row.append(marker);
      const detail = node('div','activity-body'), title = node('div','activity-title');
      title.append(node('strong','',label(c.action)),badge(label(c.status),tone,false));
      detail.append(title,node('p','',label(c.result)),node('time','',formatTime(c.created)));
      row.append(detail); return row;
    });
    list.replaceChildren(...(rows.length ? rows : [emptyState('尚无活动记录','验证邮箱连接后，结果会显示在这里。')]));
  }
  function hidden(name, value) { const el = node('input'); el.type='hidden'; el.name=name; el.value=value; return el; }
  function mailText(value) {
    return String(value || '').replace(/(?:https?:\/\/|www\.)[^\s<>]+/gi,'（链接已隐藏）')
      .replace(/([A-Za-z0-9_.+%-])[A-Za-z0-9_.+%-]*@([A-Za-z0-9.-]+)/g,'$1***@$2');
  }
  let taskSignature=null;
  function renderTasks(tasks) {
    const list = $('[data-tasks]'); if(!list) return;
    const signature=JSON.stringify(tasks); if(taskSignature===signature) return;
    const opened=new Set(all('.task-row[open]',list).map(el=>el.dataset.taskId));
    const top=list.scrollTop; taskSignature=signature;
    updateText('[data-task-count]',tasks.length+' 条');
    const rows=tasks.map(task=>{
      const row=node('details','task-row'); row.dataset.taskId=task.id; row.open=opened.has(task.id);
      const trigger=node('summary','task-trigger'), heading=node('div','task-heading');
      const arrow=node('span','task-chevron'); arrow.append(smallIcon(false));
      // Reuse a simple chevron path with the same local icon stroke.
      const path=arrow.querySelector('path'); path.setAttribute('d','m9 5 7 7-7 7');
      heading.append(node('strong','task-subject',mailText(task.subject || '邮件摘要暂不可用')),badge(label(task.status),toneFor(task.status)),arrow);
      const meta=node('div','task-meta');
      meta.append(node('span','',mailText(task.sender || '发件人未留存')),node('time','','收件 '+formatTime(task.received)),node('span','','尝试 '+task.attempts+' 次'));
      trigger.append(heading,meta); row.append(trigger);
      const expanded=node('div','task-expanded'), caption=node('div','detail-caption','正文摘要');
      caption.append(node('span','','最多 300 字 · 链接已隐藏'));
      expanded.append(caption,node('p','task-preview',mailText(task.preview || '这条旧记录尚未保存正文预览。')),node('div','detail-caption','附件信息'),node('p','task-attachments',(task.attachments || []).map(mailText).join(' · ') || '未留存附件信息或无附件'));
      const detail=node('div','task-detail');
      detail.append(node('span','','UID '+task.uid),node('span','',(task.completed?'转发于 ':'检测于 ')+formatTime(task.completed || task.detected)));
      if(task.status==='retry') detail.append(node('span','','下次重试 '+formatTime(task.next_attempt)));
      expanded.append(detail,node('code','task-id',task.id));
      if(task.error || task.notice) expanded.append(node('p','task-note',[task.error,task.notice].filter(Boolean).map(label).join(' · ')));
      if(['failed','retry','needs_review'].includes(task.status)) {
        const form=node('form'); form.method='post'; form.action=body.dataset.actionUrl; form.dataset.operation='';
        if(task.status==='needs_review') form.dataset.confirm='确认后将发送此时间异常的邮件任务。';
        const button=node('button','button button-secondary button-small',task.status==='needs_review'?'确认并发送':'重试任务');
        button.name='action'; button.value='retry'; form.append(hidden('csrf',csrf),hidden('task_id',task.id),button); expanded.append(form);
      }
      row.append(expanded); return row;
    });
    list.replaceChildren(...(rows.length?rows:[emptyState('等待第一封新邮件','新的邮件任务会自动出现在这里。')]));
    list.scrollTop=top;
  }
  const settingsForm=$('[data-settings-form]');
  function formFingerprint(form) {
    return JSON.stringify([...form.elements].filter(el=>el.name && el.name!=='csrf' && ['INPUT','SELECT'].includes(el.tagName))
      .map(el=>[el.name,el.type==='checkbox'?el.checked:el.value]));
  }
  let savedFingerprint=settingsForm?formFingerprint(settingsForm):null, saveResult=null;
  function settingsDirty() { return !!settingsForm && formFingerprint(settingsForm)!==savedFingerprint; }
  function updateSaveState() {
    if(!settingsForm) return;
    const button=$('button[type="submit"]',settingsForm), bar=$('.save-bar',settingsForm), dirty=settingsDirty();
    const busy=button.dataset.submitting==='true', locked=!!(currentState?.running || currentState?.sending_active);
    button.disabled=busy || locked;
    bar.classList.toggle('is-dirty',dirty); bar.classList.toggle('is-failed',saveResult==='failed');
    updateText('[data-save-state]',busy?'正在保存':saveResult==='failed'?'保存失败，更改已保留':dirty?'未保存更改':saveResult==='saved'?'保存成功':'全部更改已保存');
    updateText('[data-save-hint]',locked?'请先停止监听，等待当前发送完成后再保存。':busy?'正在更新服务器配置。':dirty?'保存后才会生效；不会自动改变当前监听状态。':'保存后重新验证连接，再启动监听。');
  }
  async function saveSettings(form) {
    const button=$('button[type="submit"]',form); if(button.dataset.submitting==='true') return;
    const submittedFingerprint=formFingerprint(form);
    const restoreButton=buttonProgress(button,'保存中');
    button.dataset.submitting='true'; saveResult=null; updateSaveState();
    try {
      const response=await request(form.getAttribute('action') || window.location.href,{method:'POST',body:new FormData(form)});
      const documentCopy=new DOMParser().parseFromString(await response.text(),'text/html');
      const result=$('[data-save-result]',documentCopy);
      if(!result || result.dataset.saveResult!=='saved') { saveResult='failed'; toast(result?.textContent.trim() || '保存失败，请检查配置后重试。','danger'); }
      else {
        savedFingerprint=submittedFingerprint; saveResult='saved'; toast(result.textContent.trim(),'success');
        const saved=$('[data-saved-consent]',documentCopy), target=$('[data-saved-consent]');
        if(saved && target) { target.textContent=saved.textContent; target.className=saved.className; }
      }
    } catch(_) { saveResult='failed'; toast('保存结果未能确认，更改已保留。请重新加载已保存配置核对后再重试。','danger'); }
    finally { button.dataset.submitting='false'; restoreButton(); updateSaveState(); }
  }
  if(settingsForm) {
    settingsForm.addEventListener('input',()=>{ saveResult=null; updateSaveState(); });
    settingsForm.addEventListener('change',()=>{ saveResult=null; updateSaveState(); });
    window.addEventListener('beforeunload',event=>{ if(settingsDirty()) { event.preventDefault(); event.returnValue=''; } });
  }
  all('[data-open-credentials]').forEach(link=>link.addEventListener('click',()=>{ const guide=$('#credential-guide'); if(guide) guide.open=true; }));
  let statusPromise = null, commandsSeen = null, currentState = null;
  function applyStatus(snapshot) {
    const state=snapshot.state, commands=snapshot.commands;
    currentState=state;
    const alive=Date.now()/1000-(state.heartbeat || 0)<90;
    all('[data-service-dot]').forEach(el=>el.className='status-dot ' + (alive ? 'success':'danger'));
    updateText('[data-service-label]',alive ? '服务在线' : '服务离线');
    all('[data-service-badge]').forEach(el => { el.className='badge badge-' + (alive ? 'success':'danger'); el.replaceChildren(node('span','status-dot'),document.createTextNode(alive?'服务在线':'服务离线')); });
    updateBadge('[data-imap-status]',state.imap_error ? '连接异常' : alive && state.imap_connected ? '已连接' : '未连接',state.imap_error ? 'danger' : alive && state.imap_connected ? 'success' : 'neutral');
    updateText('[data-imap-detail]',state.imap_error ? label(state.imap_error) : '只读访问 INBOX');
    const latestSmtp = commands.find(c=>['test_smtp','test_mail'].includes(c.action));
    const failedTest = latestSmtp?.status === 'failed' && latestSmtp.created > (state.last_smtp_ok || 0);
    const smtpError = failedTest ? latestSmtp.result : state.smtp_error;
    updateBadge('[data-smtp-status]',smtpError ? '验证失败' : state.last_smtp_ok ? '验证成功' : '未验证',smtpError ? 'danger' : state.last_smtp_ok ? 'success' : 'neutral');
    updateText('[data-smtp-detail]',smtpError ? label(smtpError) : state.last_smtp_ok ? body.dataset.page==='settings' ? 'SMTP 认证已通过' : '最近验证 ' + formatTime(state.last_smtp_ok) : '请测试 QQ SMTP 连接');
    const listenerLabel=state.running ? alive ? '运行中' : '等待服务恢复' : '已停止';
    updateBadge('[data-listener-status]',listenerLabel,state.running ? alive ? 'success':'warning':'neutral');
    updateText('[data-listener-detail]',state.sending_active ? '当前正在完成发送或测试' : '新邮件按 UID 自动检测');
    updateText('[data-listener-mode]',state.listener_type === 'IDLE' ? 'IMAP IDLE' : state.listener_type === 'poll' ? 'Polling' : '尚未确定');
    updateText('[data-last-check]',formatTime(state.last_check)); updateText('[data-last-forward]',formatTime(state.last_forward));
    updateText('[data-update-label]','状态已更新 · 每 15 秒刷新');
    all('[data-stat]').forEach(el=>el.textContent=snapshot.stats[el.dataset.stat]);
    const control=$('[data-listener-control] button');
    if (control && !control.disabled) {
      control.value=state.running ? 'stop':'start'; control.className='button ' + (state.running?'button-danger':'button-primary');
      const labelNode=$('[data-control-label]',control); if(labelNode) labelNode.textContent=state.running?'停止监听':'启动监听';
      const svg=$('[data-control-icon] svg',control);
      if (svg) { const path=document.createElementNS('http://www.w3.org/2000/svg','path'); path.setAttribute('d',state.running?'M5 5h14v14H5Z':'m8 5 11 7-11 7Z'); svg.replaceChildren(path); }
    }
    const imapTest=commands.find(c=>c.action==='test_imap' && c.status==='done');
    updateText('[data-imap-check-time]',state.last_check?'最近检查 '+formatTime(state.last_check):imapTest?'最近测试 '+formatTime(imapTest.created):'尚无检查记录');
    updateText('[data-smtp-check-time]',state.last_smtp_ok?'最近验证 '+formatTime(state.last_smtp_ok):'尚无验证记录');
    updateSaveState();
    if(commandsSeen) commands.forEach(c=>{
      if(commandsSeen.has(c.id) && commandsSeen.get(c.id)!==c.status && ['done','failed'].includes(c.status)) toast(label(c.action)+'：'+label(c.result),c.status==='failed'?'danger':'success');
    });
    commandsSeen=new Map(commands.map(c=>[c.id,c.status]));
    renderCommands(commands); renderTasks(snapshot.tasks);
  }
  async function refreshStatus(manual=false) {
    if (!body.dataset.statusUrl || sessionExpired) return;
    if (statusPromise) return statusPromise;
    const buttons=all('[data-refresh]'); buttons.forEach(b=>b.classList.add('is-busy'));
    statusPromise=(async()=>{
      try {
        const response=await request(body.dataset.statusUrl);
        applyStatus(await response.json());
        if(manual) toast('运行状态已刷新');
      } catch (_) {
        if (sessionExpired) return;
        updateText('[data-service-label]','状态获取失败');
        all('[data-service-dot]').forEach(el=>el.className='status-dot warning');
        updateText('[data-update-label]','状态更新失败，等待重试');
        if(manual) toast('状态获取失败，请检查网络后重试。','danger');
      } finally { body.classList.remove('is-loading'); buttons.forEach(b=>b.classList.remove('is-busy')); statusPromise=null; }
    })(); return statusPromise;
  }
  async function runOperation(form, button, command) {
    if(button.disabled) return;
    if(form.dataset.confirm && !(await confirmAction(form.dataset.confirm,'确认发送任务'))) return;
    const data=new FormData(form); data.set('action',command);
    const restoreButton=buttonProgress(button,'处理中');
    button.disabled=true;
    try {
      // A named action button shadows HTMLFormElement.action in the DOM.
      const response=await request(form.getAttribute('action'),{method:'POST',body:data});
      if(command==='start' && new URL(response.url).pathname.endsWith('/settings')) {
        toast('请先完成邮箱配置和转发许可。','danger'); window.location.assign(response.url); return;
      }
      toast(command==='start'?'已启用监听，等待后台连接。':command==='stop'?'已暂停监听，当前发送会安全完成。':command==='retry'?'任务已重新加入发送队列。':'已提交测试，请查看最近活动。','success');
    } catch (_) { toast('操作未能确认，请刷新状态后检查结果。','danger'); }
    finally { restoreButton(); button.disabled=false; await refreshStatus(); }
  }
  document.addEventListener('submit',event=>{
    const form=event.target;
    if(form.matches('[data-operation]')) { event.preventDefault(); const button=event.submitter || $('button',form); runOperation(form,button,button.value); }
    if(form.matches('[data-settings-form]')) {
      const mode=$('[name="mode"]',form).value, consent=$('[name="full_consent"]',form).checked;
      if(mode==='full' && !consent) { event.preventDefault(); toast('完整转发需先确认学校规定允许。','danger'); const permission=$('[name="full_consent"]',form); permission.focus(); return; }
      if(currentState?.running || currentState?.sending_active) { event.preventDefault(); toast('请先停止监听，等待当前发送完成。','danger'); return; }
      event.preventDefault(); saveSettings(form);
    }
    if(form.matches('[data-login-form]')) { const b=$('button',form); buttonProgress(b,'正在登录'); b.disabled=true; }
  });
  all('[data-command]').forEach(button=>button.addEventListener('click',async()=>{
    const settings=$('#settings-form');
    if(settings && settingsDirty() && !(await confirmAction('连接测试使用服务器已保存的配置。当前修改尚未保存，仍要继续测试吗？','测试已保存的配置'))) return;
    const form=node('form'); form.action=body.dataset.actionUrl; form.append(hidden('csrf',csrf));
    await runOperation(form,button,button.dataset.command);
  }));
  all('[data-refresh]').forEach(button=>button.addEventListener('click',async()=>{ await refreshStatus(true); if(body.dataset.page==='logs') await refreshLogs(true); }));
  all('[data-toast]').forEach(el=>toast(el.textContent.trim(),el.dataset.toast));
  function updateModeHelp() { const mode=$('#forward-mode'); if(mode) updateText('[data-mode-help]',mode.value==='full'?'保留原始正文格式；附件按下方选项处理。':'仅通知包含发件人、主题、收件时间与简短预览。'); }
  $('#forward-mode')?.addEventListener('change',updateModeHelp); updateModeHelp();
  let logs=[], visibleLogs=[], logPromise=null;
  function parseLogs(text) {
    if(text.trim()==='暂无日志' || !text.trim()) return [];
    return text.trimEnd().split('\n').map(raw=>{
      const match=raw.match(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[,\.]\d+)?)\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(.*)$/);
      return match?{time:match[1],level:match[2],message:match[3],raw}:{time:'—',level:'',message:raw,raw};
    });
  }
  function renderLogs() {
    const entries=$('#log-entries'); if(!entries) return;
    const search=$('#log-search').value.toLowerCase(), level=$('#log-level').value;
    visibleLogs=logs.filter(l=>(level==='all'||l.level===level||(level==='ERROR'&&l.level==='CRITICAL'))&&l.raw.toLowerCase().includes(search));
    $('#log-count').textContent=visibleLogs.length+' / '+logs.length+' 条';
    updateText('[data-log-stat="total"]',String(logs.length));
    updateText('[data-log-stat="warning"]',String(logs.filter(l=>l.level==='WARNING').length));
    updateText('[data-log-stat="error"]',String(logs.filter(l=>['ERROR','CRITICAL'].includes(l.level)).length));
    const latest=[...logs].reverse().find(l=>l.time!=='—');
    updateText('[data-log-stat="latest"]',latest?latest.time.slice(11,19):'—');
    const timeStat=$('[data-log-stat="latest"]'); if(timeStat) timeStat.title=latest?latest.time:'暂无事件';
    const opened=new Set(all('.log-entry[open]',entries).map(el=>el.dataset.logKey));
    const rows=visibleLogs.map(log=>{
      const row=node('details','log-entry'); row.dataset.logKey=log.raw; row.open=opened.has(log.raw);
      const summary=node('summary');
      const type=log.message.startsWith('IMAP ')?'IMAP':log.message.includes('task=')?'邮件任务':'运行事件';
      summary.append(node('time','log-time',log.time),badge(log.level || '记录',log.level==='INFO'?'info':['ERROR','CRITICAL'].includes(log.level)?'danger':log.level==='WARNING'?'warning':'neutral',false),node('span','log-event',type),node('div','log-description',log.message));
      row.append(summary,node('pre','log-full',log.raw)); return row;
    });
    entries.replaceChildren(...(rows.length?rows:[emptyState(logs.length?'没有匹配的日志':'暂无日志',logs.length?'试试其他关键词或日志级别。':'运行事件出现后会显示在这里。')]));
  }
  async function refreshLogs(manual=false) {
    if(!$('#log-source')) return;
    if(logPromise) return logPromise;
    logPromise=(async()=>{
      try {
        const response=await request(window.location.href);
        const documentCopy=new DOMParser().parseFromString(await response.text(),'text/html');
        const source=$('#log-source',documentCopy); if(!source) throw new Error('missing-log-source');
        const text=source.textContent; const entries=$('#log-entries'), top=entries.scrollTop;
        logs=parseLogs(text); renderLogs(); entries.scrollTop=top;
        $('#log-refresh-time').textContent='更新于 '+formatTime(Date.now()/1000);
      } catch (_) { if(!sessionExpired) { $('#log-refresh-time').textContent='日志更新失败，等待重试'; if(manual)toast('日志获取失败，请检查网络后重试。','danger'); } }
      finally { logPromise=null; }
    })(); return logPromise;
  }
  if($('#log-source')) {
    logs=parseLogs($('#log-source').textContent); renderLogs(); $('#log-refresh-time').textContent='更新于 '+formatTime(Date.now()/1000);
    $('#log-search').addEventListener('input',renderLogs); $('#log-level').addEventListener('change',renderLogs);
    $('#logs-auto-refresh').addEventListener('change',e=>{ if(e.target.checked)refreshLogs(); });
    $('#clear-logs').addEventListener('click',async()=>{
      if(await confirmAction('仅清空当前浏览器视图并暂停自动刷新。服务器上的日志会保留，刷新可重新读取。','清空当前日志视图？')) {
        logs=[]; renderLogs(); $('#logs-auto-refresh').checked=false; $('#log-refresh-time').textContent='已清空视图 · 自动刷新已暂停'; toast('当前视图已清空，服务器日志已保留。');
      }
    });
    $('#copy-logs').addEventListener('click',async()=>{
      if(!visibleLogs.length) { toast('当前没有可复制的日志。'); return; }
      const text=visibleLogs.map(log=>log.raw).join('\n');
      try { await navigator.clipboard.writeText(text); toast('已复制当前筛选后的日志。','success'); }
      catch (_) { toast('浏览器未允许复制，请选择日志内容后手动复制。','danger'); }
    });
  }
  if(body.dataset.statusUrl) {
    body.classList.add('is-loading'); refreshStatus();
    setInterval(()=>{ if(!document.hidden) { refreshStatus(); if($('#logs-auto-refresh')?.checked)refreshLogs(); } },15000);
    document.addEventListener('visibilitychange',()=>{ if(!document.hidden) { refreshStatus(); if($('#logs-auto-refresh')?.checked)refreshLogs(); } });
  }
})();

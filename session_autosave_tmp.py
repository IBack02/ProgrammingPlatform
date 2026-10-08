from pathlib import Path
p=Path('core/templates/core/teacher/sessions.html');s=p.read_text(encoding='utf-8')
a=s.index('      <div style="margin-top:10px;display:flex;gap:10px;flex-wrap:wrap;">');b=s.index('      <div style="margin-top:10px;">',a);s=s[:a]+s[b:]
s='\n'.join(line for line in s.split('\n') if not any(key in line for key in ('id="saveBtn"','id="saveClassesBtn"','id="startBtn"','id="stopBtn"','getElementById("startsAt").value','getElementById("endsAt").value')))
s=s.replace('{{ T.open_tasks }}','{{ T.open_modules }}')
a=s.index('  function isoToLocalInput(');b=s.index('  function statusClass(',a);s=s[:a]+s[b:]
s=s.replace('    selectedId = id;','    if (selectedId !== id && !(await flushSessionChanges())) return;\n    selectedId = id;')
s=s.replace('    renderClassesCheckboxes(res.data.class_ids || []);','    if (selectedId !== id) return;\n    renderClassesCheckboxes(res.data.class_ids || []);')
a=s.index('  document.getElementById("saveBtn").addEventListener');b=s.index('  (async function init()',a)
s=s[:a]+'''  let saveTimer = null, saveQueue = Promise.resolve(true), pendingChanges = null, failedChanges = null;
  function queueSessionChange(fields){
    if (!selectedId) return;
    const base = pendingChanges?.id === selectedId ? pendingChanges.fields : {};
    pendingChanges = {id:selectedId,fields:{...base,...fields,starts_at:null,ends_at:null}};
    setMsg("{{ T.autosaving|escapejs }}", "info");
    clearTimeout(saveTimer);
    saveTimer=setTimeout(flushSessionChanges,500);
  }
  async function flushSessionChanges(){
    clearTimeout(saveTimer);
    const change=pendingChanges || failedChanges;
    pendingChanges=null;
    if(change){
      saveQueue=saveQueue.then(async()=>{
        const fields={...(failedChanges?.id===change.id?failedChanges.fields:{}),...change.fields};
        try{
          const res=await api(`/api/teacher/sessions/${change.id}/`,"PATCH",fields);
          if(!res.data.ok)throw new Error(res.data.error||UI.saveFailed);
          failedChanges=null;
          const item=sessionsCache.find(s=>s.id===change.id);
          if(item)Object.assign(item,res.data.session);
          applySessionSearch();
          if(!pendingChanges)setMsg(UI.sessionSaved,"ok");
          return true;
        }catch(error){
          failedChanges={id:change.id,fields};
          setMsg(error.message||UI.saveFailed,"err");
          return false;
        }
      });
    }
    return saveQueue;
  }
  document.getElementById("title").addEventListener("input",event=>{
    const title=event.target.value.trim();
    queueSessionChange({title});
  });
  document.getElementById("desc").addEventListener("input",event=>queueSessionChange({description:event.target.value}));
  document.getElementById("status").addEventListener("change",event=>queueSessionChange({status:event.target.value}));
  document.getElementById("isSharedTemplate").addEventListener("change",event=>queueSessionChange({is_shared_template:event.target.checked}));
  document.getElementById("classesBox").addEventListener("change",()=>{
    queueSessionChange({class_group_ids:allClasses.filter(c=>document.getElementById("c_"+c.id)?.checked).map(c=>c.id)});
  });
  document.querySelectorAll("a[href],#tasksLink,#lessonLink").forEach(link=>link.addEventListener("click",async event=>{
    if(event.ctrlKey||event.metaKey||event.shiftKey||event.button)return;
    event.preventDefault();
    if(await flushSessionChanges())window.location.href=link.href;
  }));
  window.addEventListener("online",()=>flushSessionChanges());
  window.addEventListener("beforeunload",event=>{
    if(pendingChanges||failedChanges){event.preventDefault();event.returnValue="";}
  });

'''+s[b:]
p.write_text(s,encoding='utf-8')

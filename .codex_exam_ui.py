from pathlib import Path

def edit(path, old, new):
    p=Path(path)
    s=p.read_text(encoding='utf-8')
    assert old in s, (path, old)
    p.write_text(s.replace(old,new),encoding='utf-8')

p='core/templates/core/teacher/exams.html'
edit(p, '      <div class="section-title" style="font-size:14px;', '''      <label class="check" style="margin-top:10px"><input id="examAutoGrade" type="checkbox"><span>{{ T.exam_auto_grade }}</span></label>
      <div class="muted" style="margin-top:6px">{{ T.exam_auto_grade_help }}</div>
      <div class="section-title" style="font-size:14px;''')
edit(p, '<option value="matching">', '<option value="single_choice">{{ T.exam_test_question }}</option>\n              <option value="matching">')
edit(p, '        <div id="pairsEditor"', '''        <div id="choicesEditor" style="margin-top:12px" hidden>
          <strong>{{ T.exam_test_options }}</strong><p class="muted">{{ T.exam_test_help }}</p>
          <div id="choicesBox"></div><button id="addChoiceBtn" class="btn" type="button">{{ T.exam_add_option }}</button>
        </div>
        <div id="pairsEditor"''')
edit(p, 'function typeLabel(type){return ', 'function typeLabel(type){return type==="single_choice"?"{{ T.exam_test_question|escapejs }}":')
edit(p, 'document.getElementById("examDuration").value=current.duration_minutes;', 'document.getElementById("examAutoGrade").checked=!!current.auto_grade;document.getElementById("examAutoGrade").disabled=current.attempt_count>0;document.getElementById("examDuration").value=current.duration_minutes;')
edit(p, 'function resetQuestionForm(){', '''function addChoice(choice={}){
  const box=document.getElementById("choicesBox");if(box.children.length>=12)return;
  const row=document.createElement("div");row.className="choice-editor-row";
  row.style.cssText="display:flex;gap:10px;align-items:center;margin:8px 0";
  row.innerHTML=`<input type="radio" name="correctChoice" aria-label="{{ T.exam_correct_option|escapejs }}" ${choice.is_correct?'checked':''}>
    <textarea class="input choice-text" rows="2" maxlength="4000" aria-label="{{ T.exam_option_text|escapejs }}" style="flex:1;min-width:0">${esc(choice.text||'')}</textarea>
    <button class="btn danger" type="button" aria-label="{{ T.delete|escapejs }}">×</button>`;
  row.querySelector("button").onclick=()=>{if(box.children.length>2)row.remove();};box.appendChild(row);
}
document.getElementById("addChoiceBtn").onclick=()=>addChoice();
function resetQuestionForm(){''')
edit(p, '  editingQuestionId=null;document.getElementById("qPosition")', '  document.getElementById("choicesBox").replaceChildren();addChoice({is_correct:true});addChoice();\n  editingQuestionId=null;document.getElementById("qPosition")')
edit(p, 'document.getElementById("modelAnswerField").hidden=type==="table";', 'document.getElementById("choicesEditor").hidden=type!=="single_choice";document.getElementById("modelAnswerField").hidden=["table","single_choice"].includes(type);')
edit(p, '  if(type!=="table")payload.model_answer=', '  if(type==="single_choice")payload.choices=[...document.querySelectorAll(".choice-editor-row")].map(row=>({text:row.querySelector("textarea").value,is_correct:row.querySelector("input").checked}));\n  if(!["table","single_choice"].includes(type))payload.model_answer=')
edit(p, '  editingQuestionId=q.id;', '  document.getElementById("choicesBox").replaceChildren();(q.choices?.length?q.choices:[{is_correct:true},{}]).forEach(addChoice);\n  editingQuestionId=q.id;')
edit(p, 'if(q.question_type==="open_text")body=', 'if(["open_text","single_choice"].includes(q.question_type))body=')
edit(p, 'class_ids:selectedClasses(),is_shared_template:', 'class_ids:selectedClasses(),auto_grade:document.getElementById("examAutoGrade").checked,is_shared_template:')
edit(p, 'duration_minutes:current.duration_minutes,class_ids:', 'duration_minutes:current.duration_minutes,auto_grade:current.auto_grade,class_ids:')
edit(p, 'question_type is "open_text", "matching", "diagram", or "table".', 'question_type is "open_text", "matching", "single_choice", "diagram", or "table".')
edit(p, 'Every non-table question requires a non-empty model_answer.', '''Open text, matching and diagram questions require a non-empty model_answer.
single_choice needs choices: [{"text":"Option A","is_correct":true},{"text":"Option B","is_correct":false}] with 2–12 options and exactly one correct option; model_answer is generated automatically.
exam.auto_grade (boolean, default false) enables automatic grading of matching and single_choice questions after submission or expiry. Matching receives partial credit per correct pair.''')

p='core/templates/core/student_exams.html'
edit(p, '  </style>', '''    .choice-option{display:flex;align-items:flex-start;gap:12px;padding:14px;border:1px solid var(--line);border-radius:12px;margin:9px 0;cursor:pointer;line-height:1.5;overflow-wrap:anywhere}
    .choice-option:has(input:checked){border-color:#0284c7;background:#e0f2fe;color:#0c4a6e}.choice-option input{width:20px;height:20px;flex:none;margin:2px 0;accent-color:#0284c7}.choice-option:focus-within{outline:2px solid #38bdf8;outline-offset:2px}
    .sidebar summary{cursor:pointer}.panel,.question,.answer-slot{min-width:0}.question-prompt,.instructions,.title,.exam-card strong,.match-left{overflow-wrap:anywhere}.match-select{min-width:0;text-overflow:ellipsis}
    @media(max-width:880px){.topbar{flex-wrap:wrap;gap:8px}.topbar>div{min-width:0}.actions{justify-content:flex-end}.sidebar summary{font-size:18px}.sidebar:not([open]){padding:12px 14px}.workspace{min-height:50vh}.exam-list{max-height:40vh;overflow:auto}.answer,.match-select,.table-input{font-size:16px;min-height:44px}.match-grid{gap:6px}.match-select{margin-bottom:12px}.question{padding:13px}.question-prompt{font-size:16px}.submit-row{bottom:env(safe-area-inset-bottom,0px);padding:10px}.submit-row button{width:100%;min-height:46px}.notice{left:12px;right:12px;bottom:80px;max-width:none}.overlay{padding:0}.diagram-bar{flex-wrap:wrap;border-radius:0}.diagram-frame{flex:1;height:auto;min-height:0}.btn{min-height:42px}.timer{font-size:15px;padding:9px}.account-line{max-width:46vw;overflow-wrap:anywhere}}
    @media(max-width:420px){.topbar .actions{width:100%;justify-content:space-between}.shell{padding:8px;gap:8px}.workspace{padding:10px}.brand{font-size:18px}.question{padding:10px}.exam-head{padding-bottom:12px}.question-prompt{line-height:1.5}.account-line{max-width:100%}}
  </style>''')
edit(p, '  <aside class="panel sidebar">\n    <div class="title">{{ T.available_exams }}</div>', '  <details class="panel sidebar" id="examPicker" open>\n    <summary class="title">{{ T.available_exams }}</summary>')
edit(p, '  </aside>', '  </details>')
edit(p, 'questions=data.questions||[];', 'questions=data.questions||[];questions.forEach(q=>q.examId=id);if(matchMedia("(max-width:880px)").matches)document.getElementById("examPicker").open=false;')
edit(p, '    else if(q.question_type==="matching"){', '''    else if(q.question_type==="single_choice"){
      (q.choices||[]).forEach(choice=>{const label=document.createElement("label");label.className="choice-option";
        const radio=document.createElement("input");radio.type="radio";radio.name=`choice-${q.id}`;radio.value=choice.key;radio.disabled=closed;radio.checked=q.answer.choice_answer===choice.key;
        const text=document.createElement("span");text.textContent=choice.text;
        radio.onchange=()=>scheduleSave(q,{choice_answer:choice.key},0);label.append(radio,text);slot.appendChild(label);
      });
    }
    else if(q.question_type==="matching"){''')
# Keep local drafts authoritative and serialize saves so older responses cannot revert a new choice.
edit(p, 'function scheduleSave(q,payload,delay=700)', 'const saveChains=new Map();\nfunction scheduleSave(q,payload,delay=700)')
old='async function saveAnswer(q,payload){const data=await api(`/api/student/exams/${activeExam.id}/questions/${q.id}/answer/`,"POST",payload);const state=document.getElementById(`save-${q.id}`);state.textContent=data.ok?UI.saved:(data.error||UI.saveFailed);if(data.ok){q.answer={...q.answer,...data.answer};if(data.warning)notify(data.warning);}}'
new='''function saveAnswer(q,payload){
  const key=`${q.examId}:${q.id}`;
  const job=(saveChains.get(key)||Promise.resolve()).catch(()=>{}).then(async()=>{
    try{const data=await api(`/api/student/exams/${q.examId}/questions/${q.id}/answer/`,"POST",payload);const state=document.getElementById(`save-${q.id}`);
      if(state)state.textContent=data.ok?UI.saved:(data.error||UI.saveFailed);
      if(data.ok&&data.answer.diagram_file_url)q.answer.diagram_file_url=data.answer.diagram_file_url;
      if(data.warning)notify(data.warning);
    }catch{const state=document.getElementById(`save-${q.id}`);if(state)state.textContent=UI.saveFailed;}
  });saveChains.set(key,job);return job;
}'''
edit(p,old,new)
edit(p, 'const pending=[...saveTimers.values()];', '')
edit(p, 'question_id:q.id,text_answer:', 'question_id:q.id,choice_answer:q.answer.choice_answer||"",text_answer:')
edit(p, 'const data=await api(`/api/student/exams/${activeExam.id}/submit/`,"POST",{answers});', 'const button=document.getElementById("submitBtn");button.disabled=true;await Promise.all(saveChains.values());let data;try{data=await api(`/api/student/exams/${activeExam.id}/submit/`,"POST",{answers});}catch{notify(UI.saveFailed);return;}finally{button.disabled=false;}')
edit(p, '{{ request.session.student_name }} | <a href="/student/change-pin/">{{ T.change_pin }}</a>', '{{ request.session.student_name }}{% if not request.session.guest_lease_token %} | <a href="/student/change-pin/">{{ T.change_pin }}</a>{% endif %}')

translations={
'ru':{'exam_auto_grade':'Автопроверка тестов и соответствий','exam_auto_grade_help':'После сдачи или окончания времени: тест — полный балл или 0; соответствия — баллы за долю правильных пар. Остальные вопросы оцениваются вручную.','exam_test_question':'Тест (один ответ)','exam_test_options':'Варианты ответа','exam_test_help':'Добавьте от 2 до 12 вариантов и отметьте один правильный.','exam_add_option':'Добавить вариант','exam_correct_option':'Правильный ответ','exam_option_text':'Текст варианта'},
'kk':{'exam_auto_grade':'Тесттер мен сәйкестіктерді автоматты тексеру','exam_auto_grade_help':'Тапсырғаннан немесе уақыт біткеннен кейін: тест — толық балл немесе 0; сәйкестік — дұрыс жұптардың үлесіне қарай. Басқа сұрақтар қолмен бағаланады.','exam_test_question':'Тест (бір жауап)','exam_test_options':'Жауап нұсқалары','exam_test_help':'2–12 нұсқа қосып, бір дұрыс жауапты белгілеңіз.','exam_add_option':'Нұсқа қосу','exam_correct_option':'Дұрыс жауап','exam_option_text':'Нұсқа мәтіні'},
'en':{'exam_auto_grade':'Automatically grade tests and matching','exam_auto_grade_help':'After submission or expiry: tests receive full credit or 0; matching receives credit per correct pair. Other questions are graded manually.','exam_test_question':'Test (one answer)','exam_test_options':'Answer options','exam_test_help':'Add 2–12 options and select one correct answer.','exam_add_option':'Add option','exam_correct_option':'Correct answer','exam_option_text':'Option text'},
}
with Path('core/ui_translations.py').open('a',encoding='utf-8') as f:
    f.write('\nfor _lang_code, _extra in '+repr(translations)+'.items():\n    UI_TRANSLATIONS[_lang_code].update(_extra)\n')

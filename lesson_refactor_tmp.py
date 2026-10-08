from pathlib import Path

root = Path('core/templates/core')
p = root / 'student_portal.html'
s = p.read_text(encoding='utf-8')
def section(text, start, end):
    return text[text.index(start):text.index(end, text.index(start))]
theory = section(s, '  function renderTheoryBlocks(', '  function setModuleBackground(')
theory = theory.replace('      box.appendChild(wrap);', '      wrap.dataset.blockId = block.id;\n      wrap.dataset.blockType = block.block_type;\n      box.appendChild(wrap);')
quiz = section(s, '  function renderQuizQuestionsStudent(', '  function makeResizer(')
persist = section(quiz, '    box.querySelectorAll("input, textarea, select")', '\n  function collectQuizAnswers')
quiz = quiz.replace(persist, '''    box.querySelectorAll("input, textarea, select").forEach(el => {
      const persist = () => persistQuizAnswers(questions);
      el.addEventListener("input", persist);
      el.addEventListener("change", persist);
    });
  }
''')
helpers = section(s, '  function sanitizeHttpUrl(', '  const imageViewer =')
(root / '_learning_renderers.html').write_text(helpers + theory + quiz, encoding='utf-8')
s = s.replace(section(s, '  function renderTheoryBlocks(', '  function setModuleBackground('), '')
s = s.replace(section(s, '  function renderQuizQuestionsStudent(', '  function makeResizer('), '''  {% include "core/_learning_renderers.html" %}
  function persistQuizAnswers(questions){
    if (currentTaskId && currentModuleType === "theory_quiz")
      saveCachedQuizAnswers(currentTaskId, collectQuizAnswers(questions), currentModuleType);
  }
''')
s = s.replace(helpers, '')
css = section(s, '    .theoryContent{', '    .imageViewerOverlay{') + section(s, '    .quizQuestion{', '    .tournamentBracketViewport{')
(root / '_learning_styles.html').write_text(css + '''
    html[data-theme="dark"] .theoryBlock{color:var(--theme-text,#edf1f3)}
    html[data-theme="dark"] .quizQuestion,html[data-theme="dark"] .theoryBlock.media{background:var(--theme-surface,#202832)}
    .lesson-marker{background:#fde047;color:#172033;box-shadow:0 2px 0 #eab308;border-radius:2px}
''', encoding='utf-8')
for part in (section(s, '    .theoryContent{', '    .imageViewerOverlay{'), section(s, '    .quizQuestion{', '    .tournamentBracketViewport{')):
    s=s.replace(part,'')
s=s.replace('    .imageViewerOverlay{', '    {% include "core/_learning_styles.html" %}\n    .imageViewerOverlay{')
s=s.replace('      renderTheoryBlocks(res.data.module.blocks || []);', '      renderTheoryBlocks(res.data.module.blocks || []);\n      pollLessonHighlights(taskId);')
s=s.replace('    currentTaskId = taskId;', '    stopLessonHighlights();\n    currentTaskId = taskId;')
s=s.replace('  function persistQuizAnswers(questions){', '''  {% include "core/_lesson_markers.html" %}
  let lessonHighlightTimer = null, lessonHighlightToken = 0;
  function stopLessonHighlights(){ clearTimeout(lessonHighlightTimer); lessonHighlightToken++; }
  async function pollLessonHighlights(moduleId){
    const token = lessonHighlightToken;
    try {
      const res = await apiGet(`/api/student/theory-module/${moduleId}/highlights/`);
      if (token !== lessonHighlightToken) return;
      if (res.data.ok) applyLessonMarkers(res.data.ranges);
    } finally {
      if (token === lessonHighlightToken) lessonHighlightTimer = setTimeout(() => pollLessonHighlights(moduleId), 1000);
    }
  }
  function persistQuizAnswers(questions){''')
p.write_text(s, encoding='utf-8')

# Keep quiz presentation and grading identical for teacher practice and student attempts.
p=Path('core/views.py'); v=p.read_text(encoding='utf-8')
questions=section(v,'    questions_out = []\n','    return JsonResponse({\n        "ok": True,\n        "module": {\n            "id": module.id,\n            "position": module.position,\n            "title": module.title,\n            "topic": module.topic,\n            "instructions": module.instructions,')
v=v.replace(questions, '    questions_out = _public_quiz_questions(module, student_id)\n\n')
grading=section(v,'    results = []\n    correct_answers = 0\n    total_questions = 0\n','    attempt = StudentTheoryQuizAttempt.objects.create(')
v=v.replace(grading, '    results, correct_answers, total_questions, score_percent = _grade_theory_quiz(module, answers, student_id)\n\n')
grading=grading.replace('            student_answer = (student_value or "").strip()', '            student_answer = student_value.strip() if isinstance(student_value, str) else ""')
helpers='def _public_quiz_questions(module, student_id):\n'+questions+'    return questions_out\n\n\ndef _grade_theory_quiz(module, answers, student_id):\n    session = module.session\n'+grading+'    return results, correct_answers, total_questions, score_percent\n\n\n'
v=v.replace('@require_GET\ndef student_theory_quiz_detail',helpers+'@require_GET\ndef student_theory_quiz_detail')
p.write_text(v,encoding='utf-8')

# Move hosting UI and behavior out of the content editor.
p=root/'teacher/modules.html'; m=p.read_text(encoding='utf-8')
hosthtml=section(m,'        <div class="game-host">','        <div id="mindRaceEditor">')
hosthtml=hosthtml.replace('            <select id="gameClassSelect" class="select-ui" style="min-width:190px;"></select>\n','')
modal=section(m,'<div id="tournamentAnswersModal"','<script src=')
(root/'teacher/_lesson_game_host.html').write_text(hosthtml+modal,encoding='utf-8')
m=m.replace(section(m,'        <div class="game-host">','        <div id="mindRaceEditor">'),'')
m=m.replace(modal,'').replace('{% include "core/_game_avatar.html" %}','')
hostcss=section(m,'  .teacher-race-track{','  .module-json-grid{')
hostcss+='  .game-host{border:1px solid var(--border,#cbd5e1);border-radius:14px;padding:14px}\n'
hostcss+='  @media(max-width:900px){.wonder-board{grid-template-columns:1fr}}\n'
(root/'teacher/_lesson_game_styles.html').write_text(hostcss,encoding='utf-8')
m=m.replace(section(m,'  .teacher-race-track{','  .module-json-grid{'),'')
stop=section(m,'  function stopGamePolling(){','  function renderGamePrompts(')
host=section(m,'  function wonderEventText(','  function renderGameModule(')
host=host.replace('    qs("shuffleWonderQuestionsBtn").disabled = (currentGameModule?.wonder_questions || []).length < 2 || hasRunningRound || round?.status === "running";','')
host=host.replace('    const hasRunningRound = (currentGameModule?.rounds || []).some(item => item.status === "running");','')
host=host.replace('    currentGameRound = round || null;', '    currentGameRound = round || null;\n    qs("gameOpenLobbyBtn").disabled = !qs("gameClassSelect").value || !!(round && round.status !== "finished");')
actions=section(m,'  async function openGameLobby(){','  function renderMaterialBlocks(){')
actions=actions.replace('await openModule(currentSelectedItem());','await refreshLessonGame();')
events=section(m,'  qs("gameClassSelect").addEventListener("change", selectGameClass);','  {% include "core/teacher/_coding_module_script.html" %}')
events=events.replace('  qs("gameClassSelect").addEventListener("change", selectGameClass);\n','')
declarations=section(m,'  let currentGameRound = null;','  const UI = {')
(root/'teacher/_lesson_game_script.html').write_text(declarations+stop+host+actions+events,encoding='utf-8')
m=m.replace(section(m,'  function wonderEventText(','  function renderGameModule('),'').replace(stop,'').replace(section(m,'  async function openGameLobby(){','  function renderMaterialBlocks(){'),'').replace(section(m,'  qs("gameClassSelect").addEventListener("change", selectGameClass);','  {% include "core/teacher/_coding_module_script.html" %}'),'').replace(declarations,'')
m=m.replace('    stopGamePolling();\n','')
classpart=section(m,'    const classSelect = qs("gameClassSelect");','  async function addGamePrompt(')
m=m.replace(classpart,'''    qs("shuffleWonderQuestionsBtn").disabled = (module.wonder_questions || []).length < 2 || (module.rounds || []).some(row => row.status === "running");
  }

''')
p.write_text(m,encoding='utf-8')

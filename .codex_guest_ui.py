from pathlib import Path
import json

def edit(path, old, new):
    p = Path(path)
    s = p.read_text(encoding='utf-8')
    assert old in s, (path, old)
    p.write_text(s.replace(old, new), encoding='utf-8')

p = 'core/templates/core/teacher/classes.html'
edit(p, '<button class="btn" id="createBtn"', '<label style="display:flex;align-items:center;gap:8px"><input id="newClassGuest" type="checkbox"> {{ T.guest_class }}</label>\n    <button class="btn" id="createBtn"')
edit(p, '        <div id="students-${c.id}"', '''        ${c.is_guest ? `<div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap;margin-top:18px">
          <img src="/teacher/classes/${c.id}/guest-qr/" alt="QR" width="180" height="180" style="background:white;border-radius:12px">
          <div style="min-width:0;flex:1">
            <strong>{{ T.guest_class|escapejs }}</strong><p>{{ T.guest_available|escapejs }}: ${c.guest_available} / ${c.student_count}</p>
            <input aria-label="{{ T.guest_link|escapejs }}" readonly value="${escapeHtml(new URL(c.guest_url, location.origin).href)}" style="width:100%;box-sizing:border-box;padding:10px" onclick="this.select()">
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">
              <a class="btn" href="/teacher/classes/${c.id}/guest-qr/?download=1">{{ T.guest_download_qr|escapejs }}</a>
              <a class="btn" href="/teacher/students/?class_id=${c.id}">{{ T.guest_add|escapejs }}</a>
            </div><p class="muted">{{ T.guest_lifetime|escapejs }}</p>
          </div>
        </div>` : ''}
        <div id="students-${c.id}"''')
edit(p, '"POST", {name});', '"POST", {name, is_guest: document.getElementById("newClassGuest").checked});')
edit(p, '    renderList(res.data.classes || []);', '''    renderList(res.data.classes || []);
    const hasGuest = (res.data.classes || []).some(c => c.is_guest);
    document.getElementById("newClassGuest").disabled = hasGuest;
    if (hasGuest) document.getElementById("newClassGuest").checked = false;''')

p = 'core/templates/core/teacher/students.html'
edit(p, '<div class="muted">{{ T.bulk_students_pin }}</div>', '<div class="muted" id="bulkPinHint">{{ T.bulk_students_pin }}</div>')
edit(p, '  <div style="display:flex; gap:10px; flex-wrap:wrap;">\n    <input id="newName"', '  <p id="guestHint" class="muted" hidden>{{ T.guest_names_hint }}</p>\n  <div style="display:flex; gap:10px; flex-wrap:wrap;">\n    <input id="newName"')
edit(p, '  let editingStudentId = null;', '  let initialClassId = new URLSearchParams(location.search).get("class_id");\n  let editingStudentId = null;')
edit(p, '    const prev = sel.value;', '    const prev = sel.value || initialClassId;\n    initialClassId = null;')
edit(p, '    return classes;\n  }', '''    const guest = classes.find(c => String(c.id) === sel.value)?.is_guest;
    document.getElementById("newPin").hidden = !!guest;
    document.getElementById("guestHint").hidden = !guest;
    document.getElementById("bulkPinHint").hidden = !!guest;
    return classes;
  }''')
edit(p, 'return classesCache.map(c =>', 'return classesCache.filter(c => c.id === selectedId || (!c.is_guest && !classesCache.find(row => row.id === selectedId)?.is_guest)).map(c =>')
edit(p, 'data-classes="${s.id}" style="margin-top:8px"', 'data-classes="${s.id}" style="margin-top:8px;${s.is_guest ? \'display:none\' : \'\'}"')
edit(p, '<input class="input-ui" type="password" data-pin="${s.id}"', '<input class="input-ui" type="password" ${s.is_guest ? \'disabled\' : \'\'} data-pin="${s.id}"')
edit(p, 'classesCache.map(classGroup => {', 'classesCache.filter(c => c.id === primaryId || (!c.is_guest && !student.is_guest)).map(classGroup => {')
edit(p, '    if (!(pin.length === 6 && /^\\d+$/.test(pin))){', '    if (!classesCache.find(c => String(c.id) === classId)?.is_guest && !(pin.length === 6 && /^\\d+$/.test(pin))){')

translations = {
 'ru': {'guest_class':'Гостевой класс','guest_enter':'Войти в гостевой аккаунт','guest_intro':'Вам будет выдан один из свободных аккаунтов этого класса для знакомства с сайтом.','guest_busy':'Все аккаунты сейчас заняты. Попробуйте ещё раз, когда освободится место.','guest_lifetime':'Аккаунт освобождается при выходе или после 30 минут без запросов к сайту.','guest_one_class':'У учителя может быть только один гостевой класс.','guest_available':'Свободно аккаунтов','guest_link':'Общая ссылка для гостей','guest_download_qr':'Скачать QR-код','guest_add':'Добавить гостевые аккаунты','guest_names_hint':'Добавьте имена гостевых аккаунтов по одному или списком. PIN не нужен — вход через общую QR-ссылку.'},
 'kk': {'guest_class':'Қонақ сыныбы','guest_enter':'Қонақ аккаунтына кіру','guest_intro':'Сайтпен танысу үшін осы сыныптың бос аккаунты беріледі.','guest_busy':'Барлық аккаунттар қазір бос емес. Кейін қайталап көріңіз.','guest_lifetime':'Аккаунт шыққан кезде немесе сайтқа 30 минут сұрау жіберілмесе босатылады.','guest_one_class':'Мұғалімде тек бір қонақ сыныбы болуы мүмкін.','guest_available':'Бос аккаунттар','guest_link':'Қонақтарға ортақ сілтеме','guest_download_qr':'QR кодты жүктеу','guest_add':'Қонақ аккаунттарын қосу','guest_names_hint':'Қонақ аккаунттарының аттарын жеке немесе тізіммен қосыңыз. PIN қажет емес — ортақ QR сілтемесі арқылы кіріңіз.'},
 'en': {'guest_class':'Guest class','guest_enter':'Sign in to a guest account','guest_intro':'You will receive an available account from this class to explore the site.','guest_busy':'All accounts are currently occupied. Try again when a place becomes available.','guest_lifetime':'Accounts are released on sign-out or after 30 minutes without requests to the site.','guest_one_class':'Each teacher can have only one guest class.','guest_available':'Available accounts','guest_link':'Shared guest link','guest_download_qr':'Download QR code','guest_add':'Add guest accounts','guest_names_hint':'Add guest account names individually or as a list. No PIN is needed — guests enter using the shared QR link.'},
}
p = Path('core/ui_translations.py')
with p.open('a', encoding='utf-8') as f:
    f.write('\nfor _lang_code, _extra in ' + repr(translations) + '.items():\n    UI_TRANSLATIONS[_lang_code].update(_extra)\n')

/* Прогрессивное улучшение формы записи (T3.4, ADR-011).
   Без этого скрипта форма полностью работоспособна нативными инпутами.
   Здесь: единый список страниц из двух источников (камера/пикер),
   превью, удаление страниц, «＋ Ещё страница», дизейбл «Сохранить». */
(function () {
  "use strict";

  var camera = document.getElementById("camera-input");
  var picker = document.getElementById("picker-input");
  var actions = document.getElementById("file-actions");
  var cameraBtn = document.getElementById("camera-btn");
  var pickerBtn = document.getElementById("picker-btn");
  var pagesList = document.getElementById("pages");
  var saveBtn = document.getElementById("save-btn");
  var saveHelper = document.getElementById("save-helper");
  var comment = document.getElementById("f-comment");
  if (!camera || !picker || typeof DataTransfer === "undefined") return;

  /* Накопитель страниц. Отправляется через picker: перед сабмитом там
     всегда полный склеенный список, camera очищается. */
  var store = new DataTransfer();

  camera.hidden = true;
  picker.hidden = true;
  actions.hidden = false;

  cameraBtn.addEventListener("click", function () { camera.click(); });
  pickerBtn.addEventListener("click", function () { picker.click(); });

  function syncSaveButton() {
    /* Зеркало инварианта B4: disabled живёт только здесь — в исходном HTML
       кнопка активна, без JS инвариант держит сервер (ADR-011). */
    var isEmpty = store.files.length === 0 && comment.value.trim() === "";
    saveBtn.disabled = isEmpty;
    if (saveHelper) saveHelper.hidden = !isEmpty;
  }

  function removePage(index) {
    var next = new DataTransfer();
    Array.prototype.forEach.call(store.files, function (file, i) {
      if (i !== index) next.items.add(file);
    });
    store = next;
    render();
  }

  function render() {
    /* Разметка миниатюр — примитив pages/page-thumb/page-add кита v2:
       JS рендерит ровно то, что показывает кит (паритет с дизайном
       без серверного staging — решение 15.07.2026). */
    pagesList.innerHTML = "";
    Array.prototype.forEach.call(store.files, function (file, index) {
      var item = document.createElement("div");
      item.className = "page-thumb";

      var sheet = document.createElement("div");
      sheet.className = "p-sheet";
      if (file.type.indexOf("image/") === 0) {
        /* Превью кадра фоном «листа». Inline-стиль ставит скрипт по месту —
           страж запрещает inline-стили в шаблонах, не в runtime. */
        sheet.style.backgroundImage = "url(" + URL.createObjectURL(file) + ")";
      } else {
        var label = document.createElement("span");
        label.textContent = "PDF · документ";
        sheet.appendChild(label);
      }

      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "p-rm";
      remove.setAttribute("aria-label", "Убрать страницу " + (index + 1));
      remove.textContent = "×";
      remove.addEventListener("click", function () { removePage(index); });

      var caption = document.createElement("div");
      caption.className = "p-cap";
      caption.textContent = "стр. " + (index + 1);

      item.appendChild(sheet);
      item.appendChild(remove);
      item.appendChild(caption);
      pagesList.appendChild(item);
    });

    /* Цикл потока К (кит v2): «＋ ещё страница» живёт в конце превью-списка
       и снова открывает камеру — по кадру за вызов. */
    if (store.files.length) {
      var add = document.createElement("button");
      add.type = "button";
      add.className = "page-add";
      add.innerHTML = '<span class="plus">＋</span>ещё<br>страница';
      add.addEventListener("click", function () { camera.click(); });
      pagesList.appendChild(add);
    }

    picker.files = store.files;
    camera.value = "";
    syncSaveButton();
  }

  function addFiles(fileList) {
    Array.prototype.forEach.call(fileList, function (file) {
      store.items.add(file);
    });
    render();
  }

  camera.addEventListener("change", function () { addFiles(camera.files); });
  picker.addEventListener("change", function () {
    /* Выбор пользователя заменил склеенный список в picker.files —
       добавляем выбранное к накопителю, render() вернёт полный список. */
    addFiles(picker.files);
  });
  comment.addEventListener("input", syncSaveButton);

  syncSaveButton();
})();

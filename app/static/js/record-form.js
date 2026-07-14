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
    saveBtn.disabled = store.files.length === 0 && comment.value.trim() === "";
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
    pagesList.innerHTML = "";
    Array.prototype.forEach.call(store.files, function (file, index) {
      var item = document.createElement("li");
      item.className = "page";

      var thumb;
      if (file.type.indexOf("image/") === 0) {
        thumb = document.createElement("img");
        thumb.className = "page-thumb";
        thumb.alt = "";
        thumb.src = URL.createObjectURL(file);
      } else {
        thumb = document.createElement("span");
        thumb.className = "page-thumb page-thumb-doc";
        thumb.textContent = "PDF";
      }

      var name = document.createElement("span");
      name.className = "page-name";
      name.textContent = (index + 1) + ". " + file.name;

      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "page-remove";
      remove.setAttribute("aria-label", "Убрать страницу " + (index + 1));
      remove.textContent = "✕";
      remove.addEventListener("click", function () { removePage(index); });

      item.appendChild(thumb);
      item.appendChild(name);
      item.appendChild(remove);
      pagesList.appendChild(item);
    });

    /* Цикл потока К: камера отдаёт по кадру — после первого кнопка
       зовёт доснять следующую страницу. */
    cameraBtn.textContent = store.files.length ? "＋ Ещё страница" : "Сфотографировать";

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

// main.js — скрипты панели управления

document.addEventListener("DOMContentLoaded", () => {
    console.log("Панель управления загружена");

    // Пример: уведомление при нажатии на кнопку "Поделиться"
    const shareButtons = document.querySelectorAll(".btn-share");
    shareButtons.forEach(button => {
        button.addEventListener("click", () => {
            const url = button.getAttribute("data-url");
            navigator.clipboard.writeText(location.origin + url).then(() => {
                alert("Ссылка на QR-код скопирована в буфер обмена!");
            });
        });
    });
});

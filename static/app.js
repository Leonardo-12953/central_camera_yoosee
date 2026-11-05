// Mostra uma alternativa clara quando o navegador não consegue exibir o vídeo
// (por exemplo, gravações H.265/HEVC no Firefox ou no Windows sem o codec).
(function () {
    "use strict";

    function markAsFailed(shell) {
        if (shell) {
            shell.classList.add("video-failed");
        }
    }

    document.querySelectorAll(".video-shell").forEach(function (shell) {
        var video = shell.querySelector("video");
        var source = shell.querySelector("source");

        if (video) {
            video.addEventListener("error", function () {
                markAsFailed(shell);
            });
            video.addEventListener("loadeddata", function () {
                shell.classList.remove("video-failed");
            });
        }

        if (source) {
            source.addEventListener("error", function () {
                markAsFailed(shell);
            });
        }
    });

    // Aviso único sobre codecs, sem impedir a reprodução normal.
    var probe = document.createElement("video");
    var supportsHevc =
        probe.canPlayType('video/mp4; codecs="hvc1"') !== "" ||
        probe.canPlayType('video/mp4; codecs="hev1"') !== "";

    if (!supportsHevc) {
        var note = document.getElementById("codec-note");
        if (note) {
            note.hidden = false;
        }
    }
})();

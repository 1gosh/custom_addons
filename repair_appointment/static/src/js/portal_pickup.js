/** @odoo-module **/

(function () {
    function initPicker() {
        const input = document.getElementById('pickup-date-input');
        if (!input || !window.flatpickr) return;
        const token = input.dataset.token;
        if (!token) return;
        const currentDate = input.dataset.currentDate || '';
        const confirmBlock = document.getElementById('pickup-confirm-block');
        const confirmLabel = document.getElementById('pickup-confirm-label');
        const submitBtn = document.getElementById('pickup-submit-btn');
        const cancelBtn = document.getElementById('pickup-cancel-btn');

        fetch('/my/pickup/' + token + '/slots', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({jsonrpc: '2.0', method: 'call', params: {}}),
        }).then(function (resp) {
            return resp.json();
        }).then(function (payload) {
            const days = (payload && payload.result) || [];
            if (!days.length) {
                input.placeholder = 'Aucun jour disponible';
                input.disabled = true;
                return;
            }
            const disabledDates = [];
            days.forEach(function (d) {
                if (d.state !== 'open') disabledDates.push(d.date);
            });
            const first = days[0].date;
            const last = days[days.length - 1].date;
            const locale = (window.flatpickr.l10ns && window.flatpickr.l10ns.fr) || undefined;

            const fp = window.flatpickr(input, {
                locale: locale,
                dateFormat: 'Y-m-d',
                altInput: true,
                altFormat: 'l j F Y',
                minDate: first,
                maxDate: last,
                disable: disabledDates,
                defaultDate: currentDate || undefined,
                onChange: function (selectedDates, dateStr) {
                    if (!dateStr) {
                        if (confirmBlock) confirmBlock.style.display = 'none';
                        if (submitBtn) submitBtn.disabled = true;
                        return;
                    }
                    if (confirmLabel) {
                        const d = new Date(dateStr + 'T12:00:00');
                        const weekdays = ['dimanche', 'lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi'];
                        const months = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin',
                                        'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'];
                        confirmLabel.textContent = weekdays[d.getDay()] + ' ' + d.getDate() + ' '
                                                 + months[d.getMonth()] + ' ' + d.getFullYear();
                    }
                    if (confirmBlock) confirmBlock.style.display = 'block';
                    if (submitBtn) submitBtn.disabled = false;
                },
            });

            if (cancelBtn) {
                cancelBtn.addEventListener('click', function () {
                    fp.clear();
                    if (confirmBlock) confirmBlock.style.display = 'none';
                    if (submitBtn) submitBtn.disabled = true;
                });
            }
        }).catch(function () {
            input.placeholder = 'Erreur de chargement';
            input.disabled = true;
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initPicker);
    } else {
        initPicker();
    }
})();

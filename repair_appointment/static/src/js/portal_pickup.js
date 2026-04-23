/** @odoo-module **/

(function () {
    function init() {
        const container = document.getElementById('pickup-day-selector');
        if (!container) return;
        const token = container.dataset.token;
        if (!token) return;
        const currentDate = container.dataset.currentDate || '';
        const monthNames = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin',
                            'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'];
        const dayHeads = ['L', 'M', 'M', 'J', 'V', 'S', 'D'];
        const weekdays = ['dimanche', 'lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi'];

        function fmtDateLabel(iso) {
            const d = new Date(iso + 'T12:00:00');
            return weekdays[d.getDay()] + ' ' + d.getDate() + ' '
                   + monthNames[d.getMonth()] + ' ' + d.getFullYear();
        }

        function render(days) {
            const byMonth = {};
            days.forEach(function (d) {
                const k = d.date.substring(0, 7);
                (byMonth[k] = byMonth[k] || []).push(d);
            });
            const monthsOrdered = Object.keys(byMonth).sort();
            const root = document.createElement('div');
            monthsOrdered.forEach(function (monthKey) {
                const parts = monthKey.split('-').map(Number);
                const year = parts[0];
                const month = parts[1];
                const wrap = document.createElement('div');
                wrap.className = 'pickup-month';
                const title = document.createElement('h5');
                title.textContent = monthNames[month - 1] + ' ' + year;
                wrap.appendChild(title);

                const grid = document.createElement('div');
                grid.className = 'pickup-grid';
                dayHeads.forEach(function (h) {
                    const el = document.createElement('div');
                    el.className = 'pickup-dayhead';
                    el.textContent = h;
                    grid.appendChild(el);
                });

                const firstDay = byMonth[monthKey][0];
                const firstJs = new Date(firstDay.date + 'T12:00:00');
                const leading = (firstJs.getDay() + 6) % 7;
                for (let i = 0; i < leading; i++) {
                    const pad = document.createElement('div');
                    pad.className = 'pickup-day empty';
                    grid.appendChild(pad);
                }

                byMonth[monthKey].forEach(function (d) {
                    const cell = document.createElement('div');
                    cell.className = 'pickup-day ' + d.state;
                    cell.dataset.date = d.date;
                    const js = new Date(d.date + 'T12:00:00');
                    cell.textContent = js.getDate();
                    if (d.state === 'open') {
                        cell.addEventListener('click', function () {
                            document.querySelectorAll('.pickup-day.selected')
                                .forEach(function (c) { c.classList.remove('selected'); });
                            cell.classList.add('selected');
                            document.querySelectorAll('input[name="pickup_date"]')
                                .forEach(function (input) { input.value = d.date; });
                            const label = document.getElementById('pickup-confirm-label');
                            if (label) label.textContent = fmtDateLabel(d.date);
                            const block = document.getElementById('pickup-confirm-block');
                            if (block) block.style.display = 'block';
                        });
                    }
                    if (d.date === currentDate) {
                        cell.classList.add('selected');
                    }
                    grid.appendChild(cell);
                });
                wrap.appendChild(grid);
                root.appendChild(wrap);
            });
            const legend = document.createElement('div');
            legend.className = 'pickup-legend';
            const lOpen = document.createElement('span');
            lOpen.className = 'l-open';
            lOpen.textContent = 'Disponible';
            const lFull = document.createElement('span');
            lFull.className = 'l-full';
            lFull.textContent = 'Complet';
            const lClosed = document.createElement('span');
            lClosed.className = 'l-closed';
            lClosed.textContent = 'Fermé / Indisponible';
            legend.appendChild(lOpen);
            legend.appendChild(lFull);
            legend.appendChild(lClosed);
            root.appendChild(legend);
            container.innerHTML = '';
            container.appendChild(root);

            const cancelBtn = document.getElementById('pickup-cancel-btn');
            if (cancelBtn) {
                cancelBtn.addEventListener('click', function () {
                    const block = document.getElementById('pickup-confirm-block');
                    if (block) block.style.display = 'none';
                    document.querySelectorAll('.pickup-day.selected')
                        .forEach(function (c) { c.classList.remove('selected'); });
                });
            }
        }

        fetch('/my/pickup/' + token + '/slots', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({jsonrpc: '2.0', method: 'call', params: {}}),
        }).then(function (resp) {
            return resp.json();
        }).then(function (payload) {
            const days = (payload && payload.result) || [];
            if (!days.length) {
                container.textContent = 'Aucun jour disponible.';
                return;
            }
            render(days);
        }).catch(function () {
            container.textContent = 'Erreur de chargement du calendrier.';
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

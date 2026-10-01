/* Chart.js defaults matched to the theme. Categorical order follows the validated
   reference palette; status colours (ok / drift / failed) are reserved and always labelled. */
(function () {
    'use strict';
    const dark = () => document.documentElement.getAttribute('data-bs-theme') === 'dark';
    const CAT = {
        light: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948'],
        dark: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'],
    };
    const STATUS = { ok: '#1ab394', changed: '#1c84c6', drift: '#f8ac59', failed: '#ed5565', never: '#a1a9b1' };

    SW.palette = () => CAT[dark() ? 'dark' : 'light'];
    SW.status = STATUS;
    SW.ink = () => ({
        text: dark() ? '#aab8c5' : '#6c757d',
        grid: dark() ? 'rgba(255,255,255,.06)' : 'rgba(0,0,0,.06)',
        surface: dark() ? '#1e1f27' : '#ffffff',
    });

    SW.applyChartDefaults = function () {
        if (!window.Chart) return;
        const ink = SW.ink();
        Chart.defaults.font.family = '"Open Sans", sans-serif';
        Chart.defaults.font.size = 12;
        Chart.defaults.color = ink.text;
        Chart.defaults.borderColor = ink.grid;
        Chart.defaults.plugins.legend.labels.boxWidth = 10;
        Chart.defaults.plugins.legend.labels.boxHeight = 10;
        Chart.defaults.plugins.legend.labels.useBorderRadius = true;
        Chart.defaults.plugins.legend.labels.borderRadius = 3;
        Chart.defaults.plugins.tooltip.padding = 10;
        Chart.defaults.plugins.tooltip.cornerRadius = 6;
        Chart.defaults.plugins.tooltip.backgroundColor = dark() ? '#2c2d38' : '#313a46';
        Chart.defaults.elements.bar.borderRadius = 4;
        Chart.defaults.elements.line.borderWidth = 2;
        Chart.defaults.maintainAspectRatio = false;
    };
    SW.applyChartDefaults();

    SW.charts = [];
    SW.makeChart = function (canvas, config) {
        const c = new Chart(canvas, config);
        SW.charts.push({ chart: c, rebuild: () => config });
        return c;
    };
    // rebuild colours on theme switch
    document.addEventListener('sw:theme', () => {
        SW.applyChartDefaults();
        SW.charts.forEach(({ chart }) => {
            const ink = SW.ink();
            Object.values(chart.options.scales || {}).forEach(s => {
                if (s.grid) s.grid.color = ink.grid;
                if (s.ticks) s.ticks.color = ink.text;
            });
            if (chart.options.plugins?.legend?.labels) chart.options.plugins.legend.labels.color = ink.text;
            chart.data.datasets.forEach(ds => { if (ds._surfaceBorder) ds.borderColor = ink.surface; });
            chart.update();
        });
    });

    /* horizontal single-hue bar chart: [[label, value], ...] */
    SW.hbar = function (canvas, pairs, opts = {}) {
        const color = opts.color || SW.palette()[0];
        return SW.makeChart(canvas, {
            type: 'bar',
            data: {
                labels: pairs.map(p => p[0]),
                datasets: [{ label: opts.label || 'Count', data: pairs.map(p => p[1]), backgroundColor: color,
                             maxBarThickness: 18, borderRadius: 4 }],
            },
            options: {
                indexAxis: 'y',
                plugins: { legend: { display: false } },
                scales: {
                    x: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: SW.ink().grid } },
                    y: { grid: { display: false } },
                },
                onClick: opts.onClick,
            },
        });
    };
})();

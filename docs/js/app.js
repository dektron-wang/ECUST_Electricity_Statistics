/**
 * ECUST 电量统计 - 前端逻辑
 */

// 全局变量
let electricityData = null;
let currentDormIndex = 0;
let chart = null;

// 楼号映射（奉贤校区）
function buildingNumberMap(id) {
    const num = parseInt(id);
    if (num >= 27 && num <= 46) return num - 22;
    if (num >= 49 && num <= 52) return num - 24;
    return num;
}

// 获取状态样式
function getStatusClass(kwh, threshold) {
    if (kwh <= threshold * 0.5) return 'danger';
    if (kwh <= threshold) return 'warning';
    return 'normal';
}

// 获取状态文本
function getStatusText(kwh, threshold) {
    if (kwh <= threshold * 0.5) return '电量紧张';
    if (kwh <= threshold) return '注意充电';
    return '正常';
}

// 格式化日期
function formatDate(dateStr) {
    const date = new Date(dateStr);
    return `${date.getMonth() + 1}月${date.getDate()}日`;
}

// 计算变化量
function calculateChange(records, index) {
    if (index >= records.length - 1) return null;
    const current = records[index].kwh;
    const previous = records[index + 1].kwh;
    return current - previous;
}

// 初始化宿舍选择器
function initDormSelector() {
    const selector = document.getElementById('dormSelector');
    selector.innerHTML = '';

    electricityData.dormitories.forEach((dorm, index) => {
        const btn = document.createElement('button');
        btn.className = `dorm-btn ${index === currentDormIndex ? 'active' : ''}`;
        btn.textContent = dorm.name;
        btn.onclick = () => selectDorm(index);
        selector.appendChild(btn);
    });
}

// 选择宿舍
function selectDorm(index) {
    currentDormIndex = index;

    // 更新按钮状态
    document.querySelectorAll('.dorm-btn').forEach((btn, i) => {
        btn.classList.toggle('active', i === index);
    });

    // 更新显示
    updateDisplay();
}

// 更新显示
function updateDisplay() {
    const dorm = electricityData.dormitories[currentDormIndex];
    if (!dorm) return;

    const latestKwh = dorm.latest_kwh;
    const latestPower = dorm.latest_power;
    const threshold = dorm.warning_threshold;
    const records = dorm.records;

    // 更新状态卡片
    const statusBadge = document.getElementById('statusBadge');
    statusBadge.textContent = getStatusText(latestKwh, threshold);
    statusBadge.className = `status-badge ${getStatusClass(latestKwh, threshold)}`;

    document.getElementById('currentKwh').textContent = latestKwh.toFixed(1);

    // 更新功率显示
    const powerElement = document.getElementById('currentPower');
    if (latestPower !== null && latestPower !== undefined) {
        powerElement.textContent = `${latestPower.toFixed(2)} kW`;
    } else {
        powerElement.textContent = '无数据';
    }

    document.getElementById('dormName').textContent = dorm.name;
    document.getElementById('dormLocation').textContent =
        `${buildingNumberMap(dorm.buildid)}号楼 ${dorm.roomid}室`;
    document.getElementById('updateTime').textContent = electricityData.updated_at;

    // 更新图表
    updateChart(records);

    // 更新表格
    updateTable(records);
}

// 更新图表
function updateChart(records) {
    const ctx = document.getElementById('electricityChart').getContext('2d');

    const labels = records.map(r => formatDate(r.time));
    const data = records.map(r => r.kwh);

    if (chart) {
        chart.destroy();
    }

    const isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const textColor = isDark ? '#94a3b8' : '#64748b';
    const gridColor = isDark ? '#334155' : '#e2e8f0';

    chart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: '剩余电量 (kWh)',
                data: data,
                borderColor: '#3b82f6',
                backgroundColor: 'rgba(59, 130, 246, 0.1)',
                borderWidth: 2,
                fill: true,
                tension: 0.3,
                pointRadius: 4,
                pointBackgroundColor: '#3b82f6',
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    callbacks: {
                        label: function(context) {
                            return `${context.parsed.y.toFixed(1)} kWh`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: {
                        color: gridColor
                    },
                    ticks: {
                        color: textColor
                    }
                },
                y: {
                    beginAtZero: false,
                    grid: {
                        color: gridColor
                    },
                    ticks: {
                        color: textColor,
                        callback: function(value) {
                            return value + ' kWh';
                        }
                    }
                }
            }
        }
    });
}

// 更新表格
function updateTable(records) {
    const tbody = document.getElementById('dataTable');
    tbody.innerHTML = '';

    // 按时间降序显示
    const sortedRecords = [...records].reverse();

    sortedRecords.forEach((record, index) => {
        const tr = document.createElement('tr');

        const change = calculateChange(records, records.length - 1 - index);
        let changeText = '--';
        let changeClass = 'change-none';

        if (change !== null) {
            if (change > 0) {
                changeText = `+${change.toFixed(1)} kWh`;
                changeClass = 'change-up';
            } else if (change < 0) {
                changeText = `${change.toFixed(1)} kWh`;
                changeClass = 'change-down';
            } else {
                changeText = '0 kWh';
            }
        }

        tr.innerHTML = `
            <td>${index + 1}</td>
            <td>${record.time}</td>
            <td>${record.kwh.toFixed(1)} kWh</td>
            <td class="${changeClass}">${changeText}</td>
        `;
        tbody.appendChild(tr);
    });
}

// 加载数据
async function loadData() {
    try {
        const response = await fetch('data.json');
        electricityData = await response.json();

        initDormSelector();
        updateDisplay();
    } catch (error) {
        console.error('加载数据失败:', error);
        document.getElementById('statusCard').innerHTML = `
            <div style="text-align: center; padding: 40px; color: var(--text-secondary);">
                <p>数据加载失败</p>
                <p style="font-size: 0.85rem; margin-top: 8px;">请稍后重试</p>
            </div>
        `;
    }
}

// 初始化
document.addEventListener('DOMContentLoaded', loadData);

// 监听主题变化
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (electricityData) {
        updateChart(electricityData.dormitories[currentDormIndex].records);
    }
});

// ===== DOM加载完成后初始化 =====
document.addEventListener('DOMContentLoaded', function() {
  initTabNavigation();
  initCycleComparisonChart();
  initTrendCharts();
});

// ===== TAB导航切换 =====
function initTabNavigation() {
  const tabButtons = document.querySelectorAll('.tab-btn');
  const tabPanels = document.querySelectorAll('.tab-panel');
  
  tabButtons.forEach(button => {
    button.addEventListener('click', function() {
      const targetTab = this.getAttribute('data-tab');
      
      // 移除所有active状态
      tabButtons.forEach(btn => btn.classList.remove('active'));
      tabPanels.forEach(panel => panel.classList.remove('active'));
      
      // 添加active状态到当前选中的TAB
      this.classList.add('active');
      document.getElementById(targetTab).classList.add('active');
      
      // 如果切换到TAB3，重新绘制趋势图
      if (targetTab === 'tab3') {
        setTimeout(() => initTrendCharts(), 100);
      }
    });
  });
}

// ===== TAB 1: 折线图绘制 =====
function initCycleComparisonChart() {
  const canvas = document.getElementById('cycleComparisonChart');
  if (!canvas) return;
  
  const ctx = canvas.getContext('2d');
  const container = canvas.parentElement;
  const rect = container.getBoundingClientRect();
  canvas.width = rect.width - 40;
  canvas.height = 400;
  
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  
  const activities = ['计划周期', '分析等待', '分析周期', '开发等待', '开发周期', '验收等待', '验收周期'];
  const fastLaneData = [39.52, 24.21, 4.64, 24.82, 20.81, 14.64, 5.44];
  const standardData = [44.85, 23.20, 6.58, 37.23, 32.04, 14.17, 6.63];
  const lightData = [57.96, 16.79, 5.76, 30.86, 21.30, 12.71, 7.03];
  
  const margin = { top: 40, right: 30, bottom: 60, left: 60 };
  const chartWidth = canvas.width - margin.left - margin.right;
  const chartHeight = canvas.height - margin.top - margin.bottom;
  
  const maxValue = Math.max(...fastLaneData, ...standardData, ...lightData);
  const xStep = chartWidth / (activities.length - 1);
  const yScale = chartHeight / maxValue;
  
  // 绘制网格线
  ctx.strokeStyle = '#e2e8f0';
  ctx.lineWidth = 1;
  ctx.setLineDash([5, 5]);
  
  const yTicks = 5;
  for (let i = 0; i <= yTicks; i++) {
    const y = margin.top + (chartHeight / yTicks) * i;
    ctx.beginPath();
    ctx.moveTo(margin.left, y);
    ctx.lineTo(margin.left + chartWidth, y);
    ctx.stroke();
    
    const value = Math.round(maxValue - (maxValue / yTicks) * i);
    ctx.fillStyle = '#64748b';
    ctx.font = '12px sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(value.toString(), margin.left - 10, y + 4);
  }
  
  ctx.setLineDash([]);
  
  // 绘制X轴标签
  ctx.fillStyle = '#475569';
  ctx.font = '12px sans-serif';
  ctx.textAlign = 'center';
  activities.forEach((activity, index) => {
    const x = margin.left + index * xStep;
    ctx.fillText(activity, x, margin.top + chartHeight + 20);
  });
  
  function drawLine(data, color, pointRadius) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 3;
    ctx.setLineDash([]);
    
    ctx.beginPath();
    data.forEach((value, index) => {
      const x = margin.left + index * xStep;
      const y = margin.top + chartHeight - value * yScale;
      
      if (index === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();
    
    data.forEach((value, index) => {
      const x = margin.left + index * xStep;
      const y = margin.top + chartHeight - value * yScale;
      
      ctx.beginPath();
      ctx.arc(x, y, pointRadius, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2;
      ctx.stroke();
      
      ctx.fillStyle = color;
      ctx.font = 'bold 11px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(value.toFixed(1), x, y - 12);
    });
  }
  
  drawLine(fastLaneData, '#2563eb', 5);
  drawLine(standardData, '#7c3aed', 5);
  drawLine(lightData, '#059669', 5);
  
  const legendContainer = document.getElementById('chartLegend1');
  if (legendContainer) {
    legendContainer.innerHTML = `
      <div class="legend-item">
        <div class="legend-color fast-lane"></div>
        <span>快车道</span>
      </div>
      <div class="legend-item">
        <div class="legend-color standard"></div>
        <span>标准</span>
      </div>
      <div class="legend-item">
        <div class="legend-color light"></div>
        <span>轻量</span>
      </div>
    `;
  }
  
  ctx.fillStyle = '#1e293b';
  ctx.font = 'bold 14px sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText('各需求类型活动周期对比（单位：天）', canvas.width / 2, 20);
}

// ===== TAB 3: 时间序列趋势图 =====
function initTrendCharts() {
  // 快车道趋势图
  drawTrendChart('fastLaneTrendChart', 'fastLaneLegend', {
    'MR分析周期': {
      data: [1.36, 5.17, 5.65, 12.68, 3.3, 3.28, 7.97, 7.99, 5.45, 10.86, 2.33, 18.31, 1.0],
      color: '#2563eb'
    },
    'MR分析等待周期': {
      data: [30.62, 23.33, 24.73, 44.24, 14.67, 36.96, 15.97, 24.23, 21.48, 22.45, 15.7, 33.55, 10.61],
      color: '#3b82f6'
    },
    'MR开发周期': {
      data: [19.49, 12.5, 5.06, 17.66, 19.92, 4.72, 1.67, 8.43, 8.57, 13.92, 25.22, 31.16, 36.5],
      color: '#7c3aed'
    },
    'MR开发等待周期': {
      data: [42.83, 23.13, 20.24, 12.61, 16.14, 10.86, 7.71, 1.11, 1.5, 21.53, 46.69, 54.87, 58.0],
      color: '#ea580c'
    },
    'MR验收周期': {
      data: [4.31, 8.22, 6.33, 8.06, 8.12, 8.4, 8.0, 11.0, 5.12, 4.91, 6.48, 6.89, 6.79],
      color: '#16a34a'
    },
    'MR验收等待周期': {
      data: [18.14, 13.78, 3.67, 5.56, 12.02, 9.84, 23.0, 14.0, 9.14, 6.96, 11.3, 14.31, 17.72],
      color: '#0891b2'
    }
  });
  
  // 标准类型趋势图
  drawTrendChart('standardTrendChart', 'standardLegend', {
    'MR分析周期': {
      data: [1.36, 5.17, 5.65, 12.68, 3.3, 3.28, 7.97, 7.99, 5.45, 10.86, 2.33, 18.31, 1.0],
      color: '#2563eb'
    },
    'MR分析等待周期': {
      data: [30.62, 23.33, 24.73, 44.24, 14.67, 36.96, 15.97, 24.23, 21.48, 22.45, 15.7, 33.55, 10.61],
      color: '#3b82f6'
    },
    'MR开发周期': {
      data: [19.49, 12.5, 5.06, 17.66, 19.92, 4.72, 1.67, 8.43, 8.57, 13.92, 25.22, 31.16, 36.5],
      color: '#7c3aed'
    },
    'MR开发等待周期': {
      data: [42.83, 23.13, 20.24, 12.61, 16.14, 10.86, 7.71, 1.11, 1.5, 21.53, 46.69, 54.87, 58.0],
      color: '#ea580c'
    },
    'MR验收周期': {
      data: [4.31, 8.22, 6.33, 8.06, 8.12, 8.4, 8.0, 11.0, 5.12, 4.91, 6.48, 6.89, 6.79],
      color: '#16a34a'
    },
    'MR验收等待周期': {
      data: [18.14, 13.78, 3.67, 5.56, 12.02, 9.84, 23.0, 14.0, 9.14, 6.96, 11.3, 14.31, 17.72],
      color: '#0891b2'
    }
  });
  
  // 轻量类型趋势图
  drawTrendChart('lightTrendChart', 'lightLegend', {
    'MR分析周期': {
      data: [1.36, 5.17, 5.65, 12.68, 3.3, 3.28, 7.97, 7.99, 5.45, 10.86, 2.33, 18.31, 1.0],
      color: '#2563eb'
    },
    'MR分析等待周期': {
      data: [30.62, 23.33, 24.73, 44.24, 14.67, 36.96, 15.97, 24.23, 21.48, 22.45, 15.7, 33.55, 10.61],
      color: '#3b82f6'
    },
    'MR开发周期': {
      data: [19.49, 12.5, 5.06, 17.66, 19.92, 4.72, 1.67, 8.43, 8.57, 13.92, 25.22, 31.16, 36.5],
      color: '#7c3aed'
    },
    'MR开发等待周期': {
      data: [42.83, 23.13, 20.24, 12.61, 16.14, 10.86, 7.71, 1.11, 1.5, 21.53, 46.69, 54.87, 58.0],
      color: '#ea580c'
    },
    'MR验收周期': {
      data: [4.31, 8.22, 6.33, 8.06, 8.12, 8.4, 8.0, 11.0, 5.12, 4.91, 6.48, 6.89, 6.79],
      color: '#16a34a'
    },
    'MR验收等待周期': {
      data: [18.14, 13.78, 3.67, 5.56, 12.02, 9.84, 23.0, 14.0, 9.14, 6.96, 11.3, 14.31, 17.72],
      color: '#0891b2'
    }
  });
}

function drawTrendChart(canvasId, legendId, seriesData) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  
  const ctx = canvas.getContext('2d');
  const container = canvas.parentElement;
  const rect = container.getBoundingClientRect();
  canvas.width = rect.width - 40;
  canvas.height = 350;
  
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  
  const months = ['2025-07', '2025-08', '2025-09', '2025-10', '2025-11', '2025-12', 
                  '2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07'];
  
  // 收集所有数据找最大值
  let maxValue = 0;
  Object.values(seriesData).forEach(series => {
    const maxInSeries = Math.max(...series.data);
    if (maxInSeries > maxValue) maxValue = maxInSeries;
  });
  maxValue = Math.ceil(maxValue / 10) * 10; // 向上取整到10的倍数
  
  const margin = { top: 40, right: 30, bottom: 50, left: 60 };
  const chartWidth = canvas.width - margin.left - margin.right;
  const chartHeight = canvas.height - margin.top - margin.bottom;
  
  const xStep = chartWidth / (months.length - 1);
  const yScale = chartHeight / maxValue;
  
  // 绘制网格线
  ctx.strokeStyle = '#e2e8f0';
  ctx.lineWidth = 1;
  ctx.setLineDash([5, 5]);
  
  const yTicks = 5;
  for (let i = 0; i <= yTicks; i++) {
    const y = margin.top + (chartHeight / yTicks) * i;
    ctx.beginPath();
    ctx.moveTo(margin.left, y);
    ctx.lineTo(margin.left + chartWidth, y);
    ctx.stroke();
    
    const value = Math.round(maxValue - (maxValue / yTicks) * i);
    ctx.fillStyle = '#64748b';
    ctx.font = '12px sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(value.toString(), margin.left - 10, y + 4);
  }
  
  ctx.setLineDash([]);
  
  // 绘制X轴标签（显示为YYYY-MM格式，显示所有月份）
  ctx.fillStyle = '#475569';
  ctx.font = '7px sans-serif';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'top';
  // 显示所有13个月份
  months.forEach((month, index) => {
    const x = margin.left + index * xStep;
    // 直接使用完整的YYYY-MM格式（如'2025-07'）
    const fullMonth = month; // '2025-07'
    // 旋转45度避免拥挤
    ctx.save();
    ctx.translate(x, margin.top + chartHeight + 10);
    ctx.rotate(-Math.PI / 4); // -45度旋转
    ctx.fillText(fullMonth, 0, 0);
    ctx.restore();
  });
  
  // Y轴标题
  ctx.save();
  ctx.translate(15, margin.top + chartHeight / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillStyle = '#64748b';
  ctx.font = '12px sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText('周期天数', 0, 0);
  ctx.restore();
  
  // 绘制每条折线
  Object.entries(seriesData).forEach(([name, series]) => {
    ctx.strokeStyle = series.color;
    ctx.lineWidth = 2;
    ctx.setLineDash([]);
    
    ctx.beginPath();
    series.data.forEach((value, index) => {
      const x = margin.left + index * xStep;
      const y = margin.top + chartHeight - value * yScale;
      
      if (index === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();
    
    // 绘制数据点
    series.data.forEach((value, index) => {
      const x = margin.left + index * xStep;
      const y = margin.top + chartHeight - value * yScale;
      
      ctx.beginPath();
      ctx.arc(x, y, 3, 0, Math.PI * 2);
      ctx.fillStyle = series.color;
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.5;
      ctx.stroke();
    });
  });
  
  // 绘制图例
  const legendContainer = document.getElementById(legendId);
  if (legendContainer) {
    let legendHTML = '';
    Object.entries(seriesData).forEach(([name, series]) => {
      const colorHex = series.color;
      legendHTML += `
        <div class="legend-item">
          <div class="legend-color" style="background: ${colorHex}"></div>
          <span>${name}</span>
        </div>
      `;
    });
    legendContainer.innerHTML = legendHTML;
  }
  
  // 图表标题
  ctx.fillStyle = '#1e293b';
  ctx.font = 'bold 14px sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText('各周期时间序列趋势（2025-07至2026-07）', canvas.width / 2, 20);
}

// ===== 窗口大小改变时重新绘制图表 =====
window.addEventListener('resize', function() {
  const canvas = document.getElementById('cycleComparisonChart');
  if (canvas && document.getElementById('tab1').classList.contains('active')) {
    setTimeout(() => initCycleComparisonChart(), 100);
  }
  
  const fastLaneCanvas = document.getElementById('fastLaneTrendChart');
  if (fastLaneCanvas && document.getElementById('tab3').classList.contains('active')) {
    setTimeout(() => initTrendCharts(), 100);
  }
});

// ===== 工具函数 =====
function scrollToElement(elementId) {
  const element = document.getElementById(elementId);
  if (element) {
    element.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function animateValue(element, start, end, duration) {
  let startTimestamp = null;
  const step = (timestamp) => {
    if (!startTimestamp) startTimestamp = timestamp;
    const progress = Math.min((timestamp - startTimestamp) / duration, 1);
    const value = Math.floor(progress * (end - start) + start);
    element.textContent = value.toString();
    if (progress < 1) {
      window.requestAnimationFrame(step);
    }
  };
  window.requestAnimationFrame(step);
}

// ===== 表格行高亮 =====
document.addEventListener('DOMContentLoaded', function() {
  const tables = document.querySelectorAll('.data-table, .equity-table, .trend-table');
  tables.forEach(table => {
    const rows = table.querySelectorAll('tbody tr');
    rows.forEach(row => {
      row.addEventListener('mouseenter', function() {
        rows.forEach(r => r.style.transition = 'background-color 0.2s ease');
      });
    });
  });
});

// ===== 打印优化 =====
window.addEventListener('beforeprint', function() {
  const tabPanels = document.querySelectorAll('.tab-panel');
  tabPanels.forEach(panel => {
    panel.style.display = 'block';
    panel.style.pageBreakAfter = 'always';
  });
});

window.addEventListener('afterprint', function() {
  const tabPanels = document.querySelectorAll('.tab-panel');
  tabPanels.forEach(panel => {
    panel.style.display = 'none';
  });
  const activePanel = document.querySelector('.tab-panel.active');
  if (activePanel) {
    activePanel.style.display = 'block';
  }
});

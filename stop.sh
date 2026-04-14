#!/bin/bash

# 闲鱼监控系统停止脚本
# 功能：停止本地后端服务及所有爬虫子进程，同时处理 Docker 容器

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${RED}========================================${NC}"
echo -e "${RED}闲鱼监控系统 - 停止脚本${NC}"
echo -e "${RED}========================================${NC}"

STOPPED_SOMETHING=false

# ---------- 1. 停止本地 uvicorn 主进程（监听 8000 端口）----------
echo -e "\n${YELLOW}[1/3] 检查本地后端服务...${NC}"

# 查找监听 8000 端口的进程（即 uvicorn / python3 -m src.app）
LISTEN_PIDS=$(lsof -ti tcp:8000 -sTCP:LISTEN 2>/dev/null || true)

if [ -n "$LISTEN_PIDS" ]; then
    echo -e "发现监听 8000 端口的进程: ${LISTEN_PIDS}"
    for PID in $LISTEN_PIDS; do
        # 尝试获取进程组 ID，优先按进程组终止（覆盖子进程）
        PGID=$(ps -o pgid= -p "$PID" 2>/dev/null | tr -d ' ')
        if [ -n "$PGID" ] && [ "$PGID" != "0" ]; then
            echo "  终止进程组 PGID=$PGID (主进程 PID=$PID)..."
            kill -TERM -- -"$PGID" 2>/dev/null || true
        else
            echo "  终止进程 PID=$PID..."
            kill -TERM "$PID" 2>/dev/null || true
        fi
    done

    # 等待进程退出
    echo -n "  等待进程退出"
    for i in $(seq 1 10); do
        sleep 1
        REMAINING=$(lsof -ti tcp:8000 -sTCP:LISTEN 2>/dev/null || true)
        if [ -z "$REMAINING" ]; then
            echo ""
            echo -e "${GREEN}✓ 本地后端服务已停止${NC}"
            STOPPED_SOMETHING=true
            break
        fi
        echo -n "."
    done

    # 如果 10 秒后仍未退出，强制终止
    REMAINING=$(lsof -ti tcp:8000 -sTCP:LISTEN 2>/dev/null || true)
    if [ -n "$REMAINING" ]; then
        echo ""
        echo -e "${YELLOW}  进程未在 10 秒内退出，强制终止...${NC}"
        for PID in $REMAINING; do
            kill -9 "$PID" 2>/dev/null || true
        done
        sleep 1
        echo -e "${GREEN}✓ 本地后端服务已强制停止${NC}"
        STOPPED_SOMETHING=true
    fi
else
    echo -e "${GREEN}✓ 未发现本地后端服务在运行${NC}"
fi

# ---------- 2. 清理残余的爬虫子进程（spider_v2.py）----------
echo -e "\n${YELLOW}[2/3] 检查残余爬虫进程...${NC}"

SPIDER_PIDS=$(pgrep -f "spider_v2.py" 2>/dev/null || true)

if [ -n "$SPIDER_PIDS" ]; then
    echo "发现残余爬虫进程: $SPIDER_PIDS"
    for PID in $SPIDER_PIDS; do
        PGID=$(ps -o pgid= -p "$PID" 2>/dev/null | tr -d ' ')
        if [ -n "$PGID" ] && [ "$PGID" != "0" ]; then
            kill -TERM -- -"$PGID" 2>/dev/null || true
        else
            kill -TERM "$PID" 2>/dev/null || true
        fi
    done

    sleep 2

    # 检查是否还有残留
    REMAINING_SPIDERS=$(pgrep -f "spider_v2.py" 2>/dev/null || true)
    if [ -n "$REMAINING_SPIDERS" ]; then
        echo -e "${YELLOW}  爬虫进程未退出，强制终止...${NC}"
        for PID in $REMAINING_SPIDERS; do
            kill -9 "$PID" 2>/dev/null || true
        done
    fi
    echo -e "${GREEN}✓ 爬虫进程已停止${NC}"
    STOPPED_SOMETHING=true
else
    echo -e "${GREEN}✓ 未发现残余爬虫进程${NC}"
fi

# ---------- 3. 停止 Docker 容器（如果在运行）----------
echo -e "\n${YELLOW}[3/3] 检查 Docker 容器...${NC}"

if command -v docker >/dev/null 2>&1; then
    # 检查 docker compose 容器是否在运行
    CONTAINER_RUNNING=$(docker compose ps -q 2>/dev/null || true)
    if [ -n "$CONTAINER_RUNNING" ]; then
        echo "发现正在运行的 Docker 容器，正在停止..."
        docker compose down 2>/dev/null
        echo -e "${GREEN}✓ Docker 容器已停止${NC}"
        STOPPED_SOMETHING=true
    else
        # 兜底：检查是否有单独的容器名在跑
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "ai-goofish-monitor-app"; then
            echo "发现独立运行的容器 ai-goofish-monitor-app，正在停止..."
            docker stop ai-goofish-monitor-app 2>/dev/null || true
            docker rm ai-goofish-monitor-app 2>/dev/null || true
            echo -e "${GREEN}✓ Docker 容器已停止并移除${NC}"
            STOPPED_SOMETHING=true
        else
            echo -e "${GREEN}✓ 未发现相关 Docker 容器在运行${NC}"
        fi
    fi
else
    echo -e "${GREEN}✓ 未安装 Docker，跳过检查${NC}"
fi

# ---------- 完成 ----------
echo ""
echo -e "${RED}========================================${NC}"
if [ "$STOPPED_SOMETHING" = true ]; then
    echo -e "${GREEN}闲鱼监控系统已全部停止 ✓${NC}"
else
    echo -e "${GREEN}闲鱼监控系统当前未在运行${NC}"
fi
echo -e "${RED}========================================${NC}"

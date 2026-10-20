#!/bin/bash
set -e
cd ~/black-hole-ai

echo "🚀 Запуск Black Hole Studio AI..."
echo ""

echo "️  Сборка и запуск контейнеров..."
docker compose up -d --build

sleep 5

echo "⏳ Загрузка модели Qwen2.5:3b..."
docker exec ollama-bhs ollama pull qwen2.5:3b

sleep 3

if curl -s http://localhost:8000/health > /dev/null; then
    echo "✅ Бэкенд запущен"
else
    echo "❌ Ошибка. Проверь: docker compose logs backend"
    exit 1
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ ВСЁ ЗАПУЩЕНО!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📍 Сайт:     http://localhost:8000/"
echo "📍 API Docs: http://localhost:8000/docs"
echo "📍 Health:   http://localhost:8000/health"
echo "📍 SD WebUI: http://localhost:7860/"
echo ""
echo "💡 Для остановки: ./stop.sh"
echo "💡 Логи:          docker compose logs -f backend"
echo ""

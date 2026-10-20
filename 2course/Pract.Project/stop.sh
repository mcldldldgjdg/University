#!/bin/bash
cd ~/black-hole-ai

echo "🛑 Остановка Black Hole Studio AI..."
echo ""

docker compose down && echo "✅ Контейнеры остановлены" || echo "⚠️  Docker не был запущен"

echo ""
echo "✅ Всё остановлено!"
echo ""

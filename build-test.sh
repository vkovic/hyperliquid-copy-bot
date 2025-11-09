#!/bin/bash
# Quick script to test Docker build

echo "🔨 Building Docker image..."
docker build -t hyperliquid-app:test .

if [ $? -eq 0 ]; then
    echo "✅ Build successful!"
    echo ""
    echo "📊 Image details:"
    docker images hyperliquid-app:test
    echo ""
    echo "🎯 To run the container:"
    echo "   docker run --rm --env-file .env hyperliquid-app:test"
    echo ""
    echo "🧹 To clean up test image:"
    echo "   docker rmi hyperliquid-app:test"
else
    echo "❌ Build failed!"
    exit 1
fi


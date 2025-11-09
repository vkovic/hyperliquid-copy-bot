#!/bin/bash
# Quick deployment script for DigitalOcean Droplet

set -e

echo "🚀 Hyperliquid Position Copier - Droplet Deployment"
echo "=================================================="
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "📦 Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
    echo "✅ Docker installed"
else
    echo "✅ Docker already installed"
fi

# Check if docker-compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "📦 Installing docker-compose..."
    apt-get update
    apt-get install -y docker-compose
    echo "✅ docker-compose installed"
else
    echo "✅ docker-compose already installed"
fi

echo ""
echo "📝 Setting up environment..."
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo "⚠️  .env file not found. Creating from .env.example..."
        cp .env.example .env
        echo "✅ Created .env file"
        echo ""
        echo "⚠️  IMPORTANT: Edit .env with your credentials:"
        echo "   nano .env"
        echo ""
        read -p "Press Enter after editing .env file..."
    else
        echo "❌ Error: .env.example not found"
        exit 1
    fi
else
    echo "✅ .env file found"
fi

echo ""
echo "🏗️  Building Docker image..."
docker-compose build

echo ""
echo "🚀 Starting container..."
docker-compose up -d

echo ""
echo "✅ Deployment complete!"
echo ""
echo "📊 View live dashboard:"
echo "   docker attach hyperliquid-position-copier"
echo "   (Detach with: Ctrl+P, Ctrl+Q)"
echo ""
echo "📋 View logs:"
echo "   docker-compose logs -f"
echo ""
echo "🔄 Management commands:"
echo "   docker-compose restart  # Restart"
echo "   docker-compose down     # Stop"
echo "   docker-compose up -d    # Start"
echo ""
echo "🎯 Container is running in background"
echo ""


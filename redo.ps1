# docker run --rm -v "$env:USERPROFILE\.nanobot:/home/nanobottie/.nanobot" nanobot onboard
docker rm -f nanobot-gateway 2>$null
docker compose -f docker-compose.standalone.yml down
docker compose -f docker-compose.standalone.yml up -d --build
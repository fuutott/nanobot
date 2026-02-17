# docker run --rm -v "$env:USERPROFILE\.nanobot:/home/nanobottie/.nanobot" nanobot onboard
docker build -t nanobot .
docker rm -f nanobot-gateway
docker run -d --name nanobot-gateway --restart unless-stopped -v "$env:USERPROFILE\.nanobot:/home/nanobottie/.nanobot" -p 18790:18790 -p 18791:18791 nanobot gateway
cd $PSScriptRoot
docker run --rm -v .\data\:/data --network=host --entrypoint=sh osdu-etp-client -c '/bin/openETPServer space -S ws://localhost:9100/ -s demo/Volve --delete'
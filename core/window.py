"""Standalone native dashboard window over the existing local service."""
import argparse
import os

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('invalid local port')
    import webview
    webview.create_window('Memory无限状态台', f'http://127.0.0.1:{args.port}/',
        width=1180, height=760, min_size=(760,520), background_color='#f6f8f5')
    webview.start(gui='edgechromium' if os.name == 'nt' else None, private_mode=True)

if __name__ == '__main__':
    main()

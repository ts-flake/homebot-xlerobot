import argparse

from homebot.services.vision_service import VisionService


def main():
    parser = argparse.ArgumentParser(description='HomeBot vision service')
    parser.add_argument('--camera', default='head', help='camera name (config.cameras key)')
    parser.add_argument('--display', action='store_true', help='show video window')
    parser.add_argument('--addr', default=None, help='publish address override')
    args = parser.parse_args()

    service = VisionService(camera_name=args.camera, pub_addr=args.addr)
    service.start(display=args.display)


if __name__ == '__main__':
    main()

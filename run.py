from app import create_app
import sys
app = create_app()

if __name__ == '__main__':
    if len(sys.argv) == 2:
        port = sys.argv[1]
    else:
        port = 5000
    app.run(host='0.0.0.0', port=port,debug=True)

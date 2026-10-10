document.addEventListener('DOMContentLoaded', () => {
    const loginForm = document.getElementById('loginForm');
    const btnLogin = document.getElementById('btnLogin');
    const messageDiv = document.getElementById('message');

    loginForm.addEventListener('submit', async (e) => {
        e.preventDefault();

        // Deshabilitar botón durante la petición
        btnLogin.disabled = true;
        btnLogin.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Verificando...';
        hideMessage();

        const username = document.getElementById('username').value.trim();
        const password = document.getElementById('password').value;

        try {
            // URL de tu API en Python (Flask / FastAPI)
            const response = await fetch('http://127.0.0.1:5000/api/admin/login', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    username: username,
                    password: password
                })
            });

            const data = await response.json();

            if (response.ok) {
                showMessage(' Acceso concedido. Redirigiendo al sistema...', 'success');
                // Redirección tras autenticar exitosamente
                setTimeout(() => {
                    window.location.href = '/admin/dashboard';
                }, 1500);
            } else {
                showMessage(data.message || 'Usuario o contraseña incorrectos.', 'error');
            }
        } catch (error) {
            console.error('Error de conexión:', error);
            showMessage('No se pudo conectar con el servidor backend.', 'error');
        } finally {
            // Restaurar estado del botón si no redirige inmediatamente
            btnLogin.disabled = false;
            btnLogin.innerHTML = '<i class="fa-solid fa-right-to-bracket"></i> Iniciar Sesión';
        }
    });

    function showMessage(text, type) {
        messageDiv.innerText = text;
        messageDiv.className = `message ${type}`;
    }

    function hideMessage() {
        messageDiv.className = 'message hidden';
        messageDiv.innerText = '';
    }
});